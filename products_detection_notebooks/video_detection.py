import cv2
import torch.cuda
from torchvision import transforms
from transformers import CLIPProcessor, CLIPModel
from tqdm.notebook import tqdm
from db_utils.database_handler import *

folder = "E:\\Yolo_v3_pretrained\\dataset\\videos\\"
# dictionary to map the movie title and the respective local filepath
movie_filepath_dict = {
    "iron man vs loki": folder + "Iron Man vs Loki - We have a Hulk - Suit Up Scene  "
                                 "The Avengers (2012) Movie Clip HD.mp4",
    "avengers age of ultron": folder + "Bruce Banner and Tony Stark put Jarvis Into "
                                       "Body - Avengers Age of Ultron (2015) Movie Clip HD Scene.mp4",
    "i love me": folder + "Demi Lovato - I Love Me (Official Video).mp4",
    "devil wears prada andy makeover": folder + "The Devil Wears Prada (45) Movie "
                                                "CLIP - Andy Gets a Makeover (2006) HD.mp4",
    "devil wears prada andy interview": folder + "The Devil Wears Prada (25) Movie "
                                                 "CLIP - Andys Interview (2006) HD.mp4",
    "no time to die": folder + "NO TIME TO DIE (2021)  Nuovo Trailer ITA del film con "
                               "007.mp4",
    "hot and cold": folder + "Katy Perry - Hot N Cold (Official).mp4",
    "inception": folder + "Inception - Ending.mp4",
    "the escape from limbo": folder + "The Escape from Limbo Inception ending Deja Vu.mp4"
}


def frame_to_tensor(frame: np.ndarray):
    transform = transforms.ToTensor()
    frame_t = transform(frame)
    return frame_t


def get_frame_patches(frame: np.ndarray, patch_size):
    """
    Function to split the frame into patches of size @patch_dim
    :param frame: the frame of the video
    :param patch_size: the dimension of the patches
    :return: the patches
    """
    frame_t = frame_to_tensor(frame)
    # unfold the tensor along the 0-dimension to get the batch dimension
    patches = frame_t.data.unfold(0, 3, 3)

    # create vertical patches (in the height dimension)
    patches = patches.unfold(1, patch_size, patch_size)

    # create horizontal patches (in width dimension)
    patches = patches.unfold(2, patch_size, patch_size)

    print(f"Shape of the patches = {patches.shape}")
    return patches


def load_model(model_id="openai/clip-vit-base-patch32"):
    """
    Function to load the transformer model and the respective preprocessor
    :param model_id: id of the model to load
    :return: the processor and the model requested
    """
    processor = CLIPProcessor.from_pretrained(model_id)
    model = CLIPModel.from_pretrained(model_id)
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model.to(device)
    return model, processor, device


def run_inference(model, processor, device, prompt, patches, patch_size, window, stride):
    """
    Function to run the model and get the similarity scores
    :param model: the Visual Transformer to be run
    :param processor: the processor associated to the Transformer to run
    :param device: the hardware devoted to run the model
    :param patches: the patches drawn from the frame
    :param patch_size: the size of the patches
    :param window: the amount of patches seen by the model
    :return: scores associated to the big patches
    """
    scores = torch.zeros(patches.shape[1], patches.shape[2])
    runs = torch.ones(patches.shape[1], patches.shape[2])

    for Y in range(0, patches.shape[1] - window + 1, stride):
        for X in range(0, patches.shape[2] - window + 1, stride):
            big_patch = torch.zeros(patch_size * window, patch_size * window, 3)
            patch_batch = patches[0, Y:Y + window, X:X + window]
            for y in range(window):
                for x in range(window):
                    big_patch[
                    y * patch_size:(y + 1) * patch_size, x * patch_size:(x + 1) * patch_size, :
                    ] = patch_batch[y, x].permute(1, 2, 0)
            # we preprocess the image and class label with the CLIP processor
            inputs = processor(
                images=big_patch,  # big patch image sent to CLIP
                return_tensors="pt",  # tell CLIP to return pytorch tensor
                text=prompt,  # class label sent to CLIP
                padding=True
            ).to(device)  # move to device if possible

            # calculate and retrieve similarity score
            score = model(**inputs).logits_per_image.item()
            # sum up similarity scores from current and previous big patches
            # that were calculated for patches within the current window
            scores[Y:Y + window, X:X + window] += score
            # calculate the number of runs on each patch within the current window
            runs[Y:Y + window, X:X + window] += 1
    # calculate average scores
    scores /= runs
    # clip scores
    for _ in range(3):
        scores = np.clip(scores - scores.mean(), 0, np.inf)
    # normalize scores
    scores = (scores - scores.min()) / (scores.max() - scores.min())
    return scores


def get_box(scores, patch_size, threshold):
    detection = scores > threshold
    # find box corners
    y_min, y_max = np.nonzero(detection)[:, 0].min().item(), np.nonzero(detection)[:, 0].max().item() + 1
    x_min, x_max = np.nonzero(detection)[:, 1].min().item(), np.nonzero(detection)[:, 1].max().item() + 1
    # convert from patch co-ords to pixel co-ords
    y_min *= patch_size
    y_max *= patch_size
    x_min *= patch_size
    x_max *= patch_size
    # calculate box height and width
    height = y_max - y_min
    width = x_max - x_min
    return x_min, y_min, width, height


def detect(model, processor, device, prompts, frame, patch_size=64, window=3, stride=1, threshold=0.5):
    """
    Function to the detect the objects in the frame. It uses the frames to look for the specified items.
    It creates a plot of the image containing the detected objects.
    :param model: model to run for the inference
    :param processor: processor associated to the model
    :param device: the hardware used to run the inference
    :param prompts: the objects to find in the frame
    :param frame: the specified frame
    :param patch_size: the size of the patches
    :param window: the amount of patches to search in simultaneously
    :return:
    """
    colors = ['#FAFF00', '#8CF1FF']
    # build image patches for detection
    frame_patches = get_frame_patches(frame, patch_size)
    frame_t = frame_to_tensor(frame)
    # convert image to format for displaying with matplotlib
    """
    image = np.moveaxis(frame_t.data.numpy(), 0, -1)
    X = frame_patches.shape[1]
    Y = frame_patches.shape[2]
    # initialize plot to display image + bounding boxes
    fig, ax = plt.subplots(figsize=(Y*0.5, X*0.5))
    ax.imshow(image)
    """
    # process image through object detection steps
    for i, prompt in enumerate(tqdm(prompts)):
        scores = run_inference(model, processor, device, prompt, frame_patches, patch_size, window, stride)
        x, y, width, height = get_box(scores, patch_size, threshold)
        # create the bounding box
        # rect = pltpatches.Rectangle((x, y), width, height, linewidth=3, edgecolor=colors[i], facecolor='none')
        cv2.rectangle(frame, (x, y), (x + width, y + height), [0, 255, 0])
        # add the patch to the Axes
        # ax.add_patch(rect)
    cv2.imshow("Frame", frame)


def show_video_and_detect(movie_title: str, movies_collection):
    url = movie_filepath_dict[movie_title.lower()]
    capture = cv2.VideoCapture(url)
    # For developing purpose, we are using the same fps as the detection algorithm
    # to retrieve the closest frame.
    fps = 0.1  # np.ceil(capture.get(cv2.CAP_PROP_FPS))
    # Till the end of the video
    while capture.isOpened():
        ret, frame = capture.read()

        if ret:
            cv2.imshow(movie_title, frame)
            # Press Q on keyboard to exit
            key = cv2.waitKey(25)
            if key & 0xFF == ord('q'):
                break
            elif key == 32:
                elapsed_seconds = capture.get(cv2.CAP_PROP_POS_MSEC) * 1e-3
                # debugging purpose
                # print("Elapsed seconds = {}".format(elapsed_seconds))
                elapsed_frames = int(np.floor(elapsed_seconds * fps))
                print("Elapsed frames = {}".format(elapsed_frames))
                t0 = time.time()
                # TODO Add database query
                bounding_boxes = get_frame_bounding_boxes(movies_collection, movie_title, elapsed_frames)
                print(f"Bounding boxes found = {bounding_boxes}")
                # TODO add boxes adjustments
                for bb in bounding_boxes:
                    cv2.rectangle(frame,  # frame
                                  (bb[0],  # x
                                   bb[1]),  # y
                                  (bb[0] + bb[2],  # width
                                   bb[1] + bb[3]),  # length
                                  [255, 0, 0]) # red box

                cv2.imshow(movie_title, frame)
                t1 = time.time()
                print("Time for retrieval = {%.3f}" % (t1 - t0))
                cv2.waitKey()
        else:
            break
    # When everything done, release
    # the video capture object
    capture.release()

    # Closes all the frames
    cv2.destroyAllWindows()


if __name__ == '__main__':
    """
    print("Type the title of the movie: ", end="")
    movie_title = input()
    print(movie_title)
    """
    client = db_connection('Piero_Rendina', 'R3nd1n@2021')
    movie_title = "Iron Man vs Loki"
    movies_info = client.movies.movies_info
    #get_detection_shape(movies_info, movie_title=movie_title)
    show_video_and_detect(movie_title, movies_info)
