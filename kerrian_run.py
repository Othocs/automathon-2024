import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from torchinfo import summary
import torchvision.io as io
import os
import json
from tqdm import tqdm
import csv
import timm
import wandb
import time

from PIL import Image
import torchvision.transforms as transforms

import matplotlib.pyplot as plt

def display_image(img):
    img = img.permute(1,2,0)
    plt.imshow(img)
    
def extract_frames(video_path, nb_frames=10, delta=1, timeit=False):
    # use time to measure the time it takes to resize a video
    t1 = time.time()
    reader = io.VideoReader(video_path)
    # take 10 frames uniformly sampled from the video
    frames = []
    for i in range(nb_frames):
        reader.seek(delta)
        frame = next(reader)
        frames.append(frame['data'])
    t2 = time.time()     
    video = torch.stack(frames)
    if timeit:
        print(f"read: {t2-t1}")
    return video

def smart_resize(data, size): # kudos louis
    # Prends un tensor de shape [...,C,H,W] et le resize en [...C,size,size]
    # x, y, height et width servent a faire un crop avant de resize

    full_height = data.shape[-2]
    full_width = data.shape[-1]

    if full_height > full_width:
        alt_height = size
        alt_width = int(full_width / (full_height / size))
    elif full_height < full_width:
        alt_height = int(full_height / (full_width / size))
        alt_width = size
    else:
        alt_height = size
        alt_width = size
    tr = transforms.Compose([
        transforms.Resize((alt_height, alt_width)),
        transforms.CenterCrop(size)
    ])
    return tr(data)

def resize_data(data, new_height, new_width, x=0, y=0, height=None, width=None):
    # Prends un tensor de shape [...,C,H,W] et le resize en [...,C,new_height,new_width]
    # x, y, height et width servent a faire un crop avant de resize
    
    full_height = data.shape[-2]
    full_width = data.shape[-1]
    height = full_height - y if height is None else height
    width = full_width -x if width is None else width

    ratio = new_height/new_width
    if height/width > ratio:
        expand_height = height
        expand_width = int(height / ratio)
    elif height/width < ratio:
        expand_height = int(width * ratio)
        expand_width = width
    else:
        expand_height = height
        expand_width = width
    tr = transforms.Compose([
        transforms.CenterCrop((expand_height, expand_width)),
        transforms.Resize((new_height, new_width))
    ])
    x = data[...,y:min(y+height, full_height), x:min(x+width, full_width)].clone()
    return tr(x)

dataset_dir = "./dataset"
root_dir = os.path.expanduser("./dataset/train")

nb_frames = 10

class VideoDataset(Dataset):
    """
    This Dataset takes a video and returns a tensor of shape [10, 3, 256, 256]
    That is 10 colored frames of 256x256 pixels.
    """
    def __init__(self, root_dir, dataset_choice="train", nb_frames=10):
        super().__init__()
        self.dataset_choice = dataset_choice
        if  self.dataset_choice == "test":
            self.root_dir = os.path.join(root_dir, "test_dataset")
        elif  self.dataset_choice == "experimental":
            self.root_dir = os.path.join(root_dir, "train")
        else:
            raise ValueError("choice must be 'test' or 'experimental'")

        with open(os.path.join(root_dir, "dataset.csv"), 'r') as file:
            reader = csv.reader(file)
            # read dataset.csv with id,label columns to create
            # a dict which associated label: id
            self.ids = {row[1] : row[0] for row in reader}

        if self.dataset_choice == "test":
            self.data = None
        else:
            with open(os.path.join(self.root_dir, "metadata.json"), 'r') as file:
                self.data= json.load(file)
                self.data = {k : (torch.tensor(float(1)) if v == 'fake' else torch.tensor(float(0))) for k, v in self.data.items()}

        self.video_files = [f for f in os.listdir(self.root_dir) if f.endswith('.mp4')]
        #self.video_files = [f for f in os.listdir(self.root_dir) if f.endswith('.pt')]

    def __len__(self):
        return len(self.video_files)

    def __getitem__(self, idx):
        video_path = os.path.join(self.root_dir, self.video_files[idx])
        #video, audio, info = io.read_video(video_path, pts_unit='sec')
        #video = torch.load(video_path)
        
        video = extract_frames(video_path)
        
        #video = video.permute(0,3,1,2)
        #length = video.shape[0]
        #video = video[[i*(length//(nb_frames)) for i in range(nb_frames)]]
        
        # resize the data into a reglar shape of 256x256 and normalize it
        video = smart_resize(video, 256) / 255
        #video = video / 255

        ID = self.ids[self.video_files[idx]]
        if self.dataset_choice == "test":
            return video, ID
        else:
            label = self.data[self.video_files[idx]]
            return video, label, ID



#train_dataset = VideoDataset(dataset_dir, dataset_choice="train", nb_frames=nb_frames)
#test_dataset = VideoDataset(dataset_dir, dataset_choice="test", nb_frames=nb_frames)
experimental_dataset = VideoDataset(dataset_dir, dataset_choice="experimental", nb_frames=nb_frames)

video, label, ID = experimental_dataset[10]
img = video[0]

display_image(img)
print(label)
print(video.shape)

video, label, ID = experimental_dataset[0]
img=video[0]

img=smart_resize(img, 256)
print(img.shape)
display_image(img)

class DeepfakeDetector(nn.Module):
    def __init__(self):
        super(DeepfakeDetector, self).__init__()

        # Charger ResNet34 pré-entraîné
        resnet = timm.create_model('resnet34', pretrained=True)
        
        # Remplacer les couches initiales par celles de ResNet34
        self.backbone = nn.Sequential(*list(resnet.children())[:-2])
        
        # Ajouter vos propres couches supplémentaires
        self.fc_input_size = 512 * 8 * 8  # La sortie de ResNet34
        
        self.dropout = nn.Dropout(0.5)
        self.fc = nn.Linear(self.fc_input_size, 1024)
        self.fc2 = nn.Linear(1024, 1)

    def forward(self, x):
        x = self.backbone(x[:, :, 0])
        x = torch.flatten(x, 1)
        x = self.dropout(x)
        x = F.relu(self.fc(x))
        x = self.fc2(x)
        return x

model = DeepfakeDetector()
summary(model)

loss_fn = nn.MSELoss()
model = DeepfakeDetector()
optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
epochs = 10
loader = DataLoader(experimental_dataset, batch_size=32, shuffle=True)
losses = []
accuracies = []

for epoch in range(epochs):
    epoch_loss = 0.0
    correct = 0
    total = 0
    for sample in tqdm(loader, desc="Epoch {}".format(epoch), ncols=0):
        optimizer.zero_grad()
        X, label, ID = sample
        X = X.permute(0, 2, 1, 3, 4)  # Reorder dimensions to [batch_size, channels, frames, height, width]
        label = torch.unsqueeze(label, dim=1)
        label_pred = model(X)
        label_pred = label_pred.view(label_pred.size(0), -1)  # Flatten the output of conv layers
        loss = loss_fn(label, label_pred)
        loss.backward()
        optimizer.step()
        
        # Calculate accuracy
        _, predicted = torch.max(label_pred, 1)
        total += label.size(0)
        correct += (predicted == label).sum().item()
        
        epoch_loss += loss.item()
    
    # Calculate accuracy and average loss for the epoch
    accuracy = 100 * correct / total
    epoch_loss /= len(loader)
    
    # Append loss and accuracy to lists
    losses.append(epoch_loss)
    accuracies.append(accuracy)
    
    # Logging loss and accuracy
    run.log({"loss": epoch_loss, "accuracy": accuracy, "epoch": epoch})

# Plot loss and accuracy
plt.figure(figsize=(10, 5))
plt.subplot(1, 2, 1)
plt.plot(losses, label='Training Loss')
plt.xlabel('Epoch')
plt.ylabel('Loss')
plt.title('Training Loss')
plt.legend()

plt.subplot(1, 2, 2)
plt.plot(accuracies, label='Training Accuracy')
plt.xlabel('Epoch')
plt.ylabel('Accuracy')
plt.title('Training Accuracy')
plt.legend()

plt.tight_layout()
plt.show()
