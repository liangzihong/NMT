import torch
import torch.nn as nn
import torch.optim as optim
from torch.autograd import Variable
import numpy as np

import random
import sys
import os
import pickle
import copy

from config import CNNConfig
from dataset import *
from encoders import CNNEncoder
from decoders import CNNDecoder
from eval import evaluate_cnn
import utils


def train(encoder, decoder, dataloader, conf):
    encoder.train()
    decoder.train()
    enc_opt = optim.Adam(encoder.parameters(), lr=conf.lr)
    dec_opt = optim.Adam(decoder.parameters(), lr=conf.lr)
    loss_fn = nn.NLLLoss()

    total_loss = 0
    for src, src_len, ref, ref_len in dataloader:
        enc_opt.zero_grad()
        dec_opt.zero_grad()
        loss = 0

        batch_size, src_max_len = src.shape
        src = Variable(torch.LongTensor(src))
        ref = Variable(torch.LongTensor(ref))

        ref_max_len = ref.size(1)

        if conf.cuda:
            src = src.cuda()
            ref = ref.cuda()

        encoder_out, e = encoder(src)

        decoder_input = ref[:,:1]
        mask = torch.Tensor(utils.mask_matrix(ref_len, ref_max_len))
        teacher_forcing = random.random() < conf.teaching_ratio

        if teacher_forcing:
            for i in range(1, ref_max_len):
                decoder_out = decoder(decoder_input, encoder_out)
                loss += utils.batch_loss(loss_fn, decoder_out, ref[:,i], mask[:,i])
                decoder_input = ref[:,:i+1]

        else:
            for i in range(1, ref_max_len):
                decoder_out = decoder(decoder_input, encoder_out)
                loss += utils.batch_loss(loss_fn, decoder_out, ref[:,i], mask[:,i])

                _, topi = decoder_out.data.topk(1)
                decoder_input = torch.cat([decoder_input, Variable(topi)], dim=1)

        total_loss += np.asscalar(loss.data.cpu().numpy())
        loss /= float(len(ref_len))
        loss.backward()

        enc_opt.step()
        dec_opt.step()

    return total_loss / len(dataloader.dataset)


def main():
    data_folder = sys.argv[1]
    src_lang = sys.argv[2]
    ref_lang = sys.argv[3]
    conf = CNNConfig(data_folder, src_lang, ref_lang)

    src_vocab = pickle.load(open(sys.argv[4], 'rb'))
    ref_vocab = pickle.load(open(sys.argv[5], 'rb'))

    train_dataset = NMTDataset(
            load_data(conf.train_src_path), 
            load_data(conf.train_ref_path), 
            src_vocab, 
            ref_vocab)
    train_dataloader = NMTDataLoader(
            train_dataset, 
            batch_size=conf.batch_size, 
            num_workers=0, 
            shuffle=True)
    print('%d training dataset loaded.' % len(train_dataset))
    
    dev_dataset = NMTDataset(
            load_data(conf.dev_src_path), 
            load_data(conf.dev_ref_path), 
            src_vocab, 
            ref_vocab)
    dev_dataloader = NMTDataLoader(
            dev_dataset, 
            batch_size=conf.batch_size, 
            num_workers=0)
    print('%d validation dataset loaded.' % len(dev_dataset))

    save_name = conf.save_path+'/cnn'
    if os.path.exists(save_name+'_encoder'):
        encoder = torch.load(save_name+'_encoder')
        decoder = torch.load(save_name+'_decoder')
    else:
        encoder = CNNEncoder(
                conf.encoder_emb_size, 
                conf.vocab_sizes, 
                train_dataset.src_vocab, 
                conf.encoder_kernels, 
                len(conf.decoder_kernels), 
                conf.encoder_dropout)
        decoder = CNNDecoder(
                conf.decoder_emb_size, 
                len(train_dataset.ref_vocab), 
                conf.decoder_kernels, 
                conf.decoder_dropout)

    if conf.cuda:
        encoder.cuda()
        decoder.cuda()

    best_bleu = 0
    best_encoder = copy.deepcopy(encoder.state_dict())
    best_decoder = copy.deepcopy(decoder.state_dict())
    for epoch in range(conf.epochs):
        print('Epoch [{:3d}]'.format(epoch))
        train_loss = train(encoder, decoder, train_dataloader, conf)
        print('Training loss:\t%f' % train_loss)

        bleus = 0
        for _, (src, ref, cand, bleu) in enumerate(
                evaluate_cnn(encoder, decoder, dev_dataloader, conf.beam)):
            bleus += sum(bleu)
        bleus /= len(dev_dataloader.dataset)

        print('Avg BLEU score:{:8.4f}'.format(bleus))

        if bleus > best_bleu:
            best_bleu = bleus
            best_encoder = copy.deepcopy(encoder.state_dict())
            best_decoder = copy.deepcopy(decoder.state_dict())

    encoder.load_state_dict(best_encoder)
    decoder.load_state_dict(best_decoder)
    torch.save(encoder.cpu(), save_name+'_encoder')
    torch.save(decoder.cpu(), save_name+'_decoder')


if __name__ == '__main__':
    main()