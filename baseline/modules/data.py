import sys, os
import threading
import torch
import torch.nn as nn
import numpy as np
import torchaudio
import random
import math
import pydub

from torch import Tensor
from torch.utils.data import Dataset
from torch.utils.data import DataLoader

# from sklearn.model_selection import train_test_split # for Kfold cross validation

from sklearn.model_selection import train_test_split, KFold # for Kfold cross validation
from modules.vocab import Vocabulary
from modules.audio.core import load_audio
from modules.audio.parser import SpectrogramParser


class SpectrogramDataset(Dataset, SpectrogramParser):
    """
    Dataset for feature & transcript matching

    Args:
        audio_paths (list): list of audio path
        transcripts (list): list of transcript
        sos_id (int): identification of <start of sequence>
        eos_id (int): identification of <end of sequence>
        spec_augment (bool): flag indication whether to use spec-augmentation or not (default: True)
        config (DictConfig): set of configurations
        dataset_path (str): path of dataset
    """

    def __init__(
            self,
            audio_paths: list,  # list of audio paths
            transcripts: list,  # list of transcript paths
            sos_id: int,  # identification of start of sequence token
            eos_id: int,  # identification of end of sequence token
            config,  # set of arguments
            spec_augment: bool = False,  # flag indication whether to use spec-augmentation of not
            dataset_path: str = None,  # path of dataset,
            audio_extension: str = 'pcm'  # audio extension
    ) -> None:
        super(SpectrogramDataset, self).__init__(
            feature_extract_by=config.feature_extract_by, sample_rate=config.sample_rate,
            n_mels=config.n_mels, frame_length=config.frame_length, frame_shift=config.frame_shift,
            del_silence=config.del_silence, input_reverse=config.input_reverse,
            normalize=config.normalize, freq_mask_para=config.freq_mask_para,
            time_mask_num=config.time_mask_num, freq_mask_num=config.freq_mask_num,
            sos_id=sos_id, eos_id=eos_id, dataset_path=dataset_path, transform_method=config.transform_method,
            audio_extension=audio_extension
        )
        self.audio_paths = list(audio_paths)
        self.transcripts = list(transcripts)
        self.augment_methods = [self.VANILLA] * len(self.audio_paths)
        self.dataset_size = len(self.audio_paths)
        self._augment(spec_augment)
        self.shuffle()

    def __getitem__(self, idx):
        """ get feature vector & transcript """
        feature = self.parse_audio(os.path.join(self.dataset_path, self.audio_paths[idx]), self.augment_methods[idx])

        if feature is None:
            return None

        transcript, status = self.parse_transcript(self.transcripts[idx])

        if status == 'err':
            print(self.transcripts[idx])
            print(idx)
        return feature, transcript

    #ID -> 문장 단위로 하려고 sos, eos를 각 문장 앞 뒤에 추가해서 ID 최종본 완성 느낌
    def parse_transcript(self, transcript):
        """ Parses transcript """
        tokens = transcript.split(' ')
        transcript = list()

        transcript.append(int(self.sos_id))
        for token in tokens:
            try:
                transcript.append(int(token))
                status='nor'
            except:
                print(tokens)
                status='err'
        transcript.append(int(self.eos_id))

        return transcript, status

    def _augment(self, spec_augment):
        """ Spec Augmentation """
        if spec_augment:
            print("Applying Spec Augmentation...")

            for idx in range(self.dataset_size):
                self.augment_methods.append(self.SPEC_AUGMENT)
                self.audio_paths.append(self.audio_paths[idx])
                self.transcripts.append(self.transcripts[idx])

    def shuffle(self):
        """ Shuffle dataset """
        tmp = list(zip(self.audio_paths, self.transcripts, self.augment_methods))
        random.shuffle(tmp)
        self.audio_paths, self.transcripts, self.augment_methods = zip(*tmp)

    def __len__(self):
        return len(self.audio_paths)

    def count(self):
        return len(self.audio_paths)


def parse_audio(audio_path: str, del_silence: bool = False, audio_extension: str = 'pcm') -> Tensor:
    signal = load_audio(audio_path, del_silence, extension=audio_extension)
    #kaldi로 feature extraction함
    feature = torchaudio.compliance.kaldi.fbank(
        waveform=Tensor(signal).unsqueeze(0),
        num_mel_bins=80,
        frame_length=20,
        frame_shift=10,
        window_type='hamming'
    ).transpose(0, 1).numpy()

    feature -= feature.mean()
    feature /= np.std(feature)

    return torch.FloatTensor(feature).transpose(0, 1)


def load_dataset(transcripts_path):
    """
    Provides dictionary of filename and labels

    Args:
        transcripts_path (str): path of transcripts

    Returns: target_dict
        - **target_dict** (dict): dictionary of filename and labels
    """
    audio_paths = list()
    transcripts = list()
    #yj_add
    #korean_transcripts = list()
    print("os.getcwd in load_dataset", os.getcwd())
    with open(transcripts_path) as f:
        print("open transcripts_path, load data")
        for idx, line in enumerate(f.readlines()):
            try:
                audio_path, korean_transcript, transcript = line.split('\t')
                #print(korean_transcript)
            except:
                print(line)
            transcript = transcript.replace('\n', '')

            audio_paths.append(audio_path)
            transcripts.append(transcript) #숫자벡터들 값
            #korean_transcripts.append(korean_transcript)

    return audio_paths, transcripts

# 고쳐서 전체 dataset 쓰게 하기
def split_dataset(config, transcripts_path: str, vocab: Vocabulary, valid_size=0.00001):
    """
    split into training set and validation set.

    Args:
        opt (ArgumentParser): set of options
        transcripts_path (str): path of  transcripts

    Returns: train_batch_num, train_dataset_list, valid_dataset
        - **train_time_step** (int): number of time step for training
        - **trainset_list** (list): list of training dataset
        - **validset** (data_loader.MelSpectrogramDataset): validation dataset
    """

    print("split dataset start !!")
    trainset_list = list()
    validset_list = list()

    audio_paths, transcripts = load_dataset(transcripts_path)
    #print("transcripts")
    #print(transcripts)

    #8:2로 train/val split
    train_audio_paths, valid_audio_paths, train_transcripts, valid_transcripts = train_test_split(audio_paths,
                                                                                                  transcripts,
                                                                                                  test_size=valid_size)


    # audio_paths & script_paths shuffled in the same order
    # for seperating train & validation
    tmp = list(zip(train_audio_paths, train_transcripts))
    random.shuffle(tmp)
    train_audio_paths, train_transcripts = zip(*tmp)

    # seperating the train dataset by the number of workers

    train_dataset = SpectrogramDataset(
        train_audio_paths,
        train_transcripts,
        vocab.sos_id, vocab.eos_id,
        config=config,
        spec_augment=config.spec_augment,
        dataset_path=config.dataset_path,
        audio_extension=config.audio_extension,
    )

    valid_dataset = SpectrogramDataset(
        valid_audio_paths,
        valid_transcripts,
        vocab.sos_id, vocab.eos_id,
        config=config,
        spec_augment=config.spec_augment,
        dataset_path=config.dataset_path,
        audio_extension=config.audio_extension,
    )

    return train_dataset, valid_dataset

# K-fold Cross Validation
def split_and_cross_validate(config, transcripts_path: str, vocab: Vocabulary, num_folds=5):
    """
    Split the dataset into training and validation sets using k-fold cross-validation.

    Args:
        config (your configuration object): Configuration for your dataset and training.
        transcripts_path (str): Path of transcripts file.
        vocab (Vocabulary): Your vocabulary object.
        num_folds (int): Number of folds for cross-validation (default is 5).

    Returns: List of (train_dataset, valid_dataset) tuples
    """
    
    print(f"Splitting the dataset into {num_folds} folds...")

    audio_paths, transcripts = load_dataset(transcripts_path)
    
    kf = KFold(n_splits=num_folds, shuffle=True, random_state=42)

    train_datasets = []
    valid_datasets = []

    for train_indices, valid_indices in kf.split(audio_paths):
        train_audio_paths = [audio_paths[i] for i in train_indices]
        train_transcripts = [transcripts[i] for i in train_indices]
        valid_audio_paths = [audio_paths[i] for i in valid_indices]
        valid_transcripts = [transcripts[i] for i in valid_indices]
        # Shuffle the training dataset
        tmp = list(zip(train_audio_paths,train_transcripts))
        random.shuffle(tmp)
        train_audio_paths, train_transcripts = zip(*tmp)
        train_dataset = SpectrogramDataset(
            train_audio_paths,
            train_transcripts,
            vocab.sos_id, vocab.eos_id,
            config=config,
            spec_augment=config.spec_augment,
            dataset_path=config.dataset_path,
            audio_extension=config.audio_extension,
        )

        valid_dataset = SpectrogramDataset(
            valid_audio_paths,
            valid_transcripts,
            vocab.sos_id, vocab.eos_id,
            config=config,
            spec_augment=config.spec_augment,
            dataset_path=config.dataset_path,
            audio_extension=config.audio_extension,
        )
        train_datasets.append(train_dataset)
        valid_datasets.append(valid_dataset)
    
    return train_datasets, valid_datasets


def collate_fn(batch):
    pad_id = 0
    """ functions that pad to the maximum sequence length """

    def seq_length_(p):
        return len(p[0])

    def target_length_(p):
        return len(p[1])

    # sort by sequence length for rnn.pack_padded_sequence()
    try:
        batch = [i for i in batch if i != None]
        batch = sorted(batch, key=lambda sample: sample[0].size(0), reverse=True)

        seq_lengths = [len(s[0]) for s in batch]
        target_lengths = [len(s[1]) - 1 for s in batch]

        max_seq_sample = max(batch, key=seq_length_)[0]
        max_target_sample = max(batch, key=target_length_)[1]

        max_seq_size = max_seq_sample.size(0)
        max_target_size = len(max_target_sample)

        feat_size = max_seq_sample.size(1)
        batch_size = len(batch)

        seqs = torch.zeros(batch_size, max_seq_size, feat_size)

        targets = torch.zeros(batch_size, max_target_size).to(torch.long)
        targets.fill_(pad_id)

        for x in range(batch_size):
            sample = batch[x]
            tensor = sample[0]
            target = sample[1]
            seq_length = tensor.size(0)

            seqs[x].narrow(0, 0, seq_length).copy_(tensor)
            targets[x].narrow(0, 0, len(target)).copy_(torch.LongTensor(target))

        seq_lengths = torch.IntTensor(seq_lengths)
        return seqs, targets, seq_lengths, target_lengths
    except Exception as e:
        print(e)

# scp -r data.py kaic2023@49.50.175.248:~/yj/hj_data.py