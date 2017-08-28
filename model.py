from __future__ import division, print_function

from functools import partial
import os

import numpy as np
from numpy import newaxis
from sklearn.utils.extmath import row_norms
import torch
from torch import nn
from torch.autograd import Variable
from torch.utils.data.sampler import BatchSampler, RandomSampler
from torchvision.utils import save_image
import tqdm


def make_generator(latent_dim=100, num_channels=3, image_size=64):
    nz = latent_dim
    ngf = image_size
    nc = num_channels

    generator = nn.Sequential(
        # input is Z, going into a convolution
        nn.ConvTranspose2d(nz, ngf * 8, 4, 1, 0, bias=False),
        nn.BatchNorm2d(ngf * 8),
        nn.ReLU(True),
        # state size. (ngf*8) x 4 x 4
        nn.ConvTranspose2d(ngf * 8, ngf * 4, 4, 2, 1, bias=False),
        nn.BatchNorm2d(ngf * 4),
        nn.ReLU(True),
        # state size. (ngf*4) x 8 x 8
        nn.ConvTranspose2d(ngf * 4, ngf * 2, 4, 2, 1, bias=False),
        nn.BatchNorm2d(ngf * 2),
        nn.ReLU(True),
        # state size. (ngf*2) x 16 x 16
        nn.ConvTranspose2d(ngf * 2, ngf, 4, 2, 1, bias=False),
        nn.BatchNorm2d(ngf),
        nn.ReLU(True),
        # state size. (ngf) x 32 x 32
        nn.ConvTranspose2d(ngf, nc, 4, 2, 1, bias=False),
        nn.Tanh(),
        # state size. (nc) x 64 x 64
    )

    def weights_init(m):
        classname = m.__class__.__name__
        if classname.find('Conv') != -1:
            m.weight.data.normal_(0.0, 0.02)
        elif classname.find('BatchNorm') != -1:
            m.weight.data.normal_(1.0, 0.02)
            m.bias.data.fill_(0)
    generator.apply(weights_init)

    return generator


def scale_latents(pca_codes, copy=True):
    z = pca_codes.copy() if copy else pca_codes
    norms = row_norms(z)
    z[norms > 1] /= norms[:, newaxis]
    return z


def load_data(data_dir):
    all_imgs = np.load(os.path.join(data_dir, 'imgs.npy'), mmap_mode='r')
    with np.load(os.path.join(data_dir, 'pca.npz')) as d:
        latents = scale_latents(d['codes'], copy=False)
    return all_imgs, latents


def var_from_numpy(x, cuda=False, requires_grad=False):
    v = torch.from_numpy(x)
    if cuda:
        v = v.cuda()
    return Variable(v, requires_grad=requires_grad)


def train(all_imgs, init_latents, out_path='.',
          epochs=50, batch_size=256, cuda=False, latent_dim=100):
    make_var = partial(var_from_numpy, cuda=cuda)
    epoch_dir = os.path.join('.', 'glo-{}').format
    for e in range(epochs):
        d = epoch_dir(e)
        if os.path.isdir(d):
            if len(os.listdir(d)) > 0:
                raise ValueError("Directory already exists: {}".format(d))
            else:
                os.rmdir(d)

    z = make_var(init_latents.copy(), requires_grad=True)
    generator = make_generator(latent_dim=latent_dim)
    if cuda:
        generator = generator.cuda()

    opt = torch.optim.SGD([
        {'params': generator.parameters(), 'lr': 1},
        {'params': [z], 'lr': 10},
    ])

    loss_fn = nn.functional.mse_loss  # TODO: laplacian loss

    samp_latent_stds = np.random.randn(100, latent_dim).astype(np.float32)

    epoch_t = tqdm.trange(epochs, desc='Epoch')
    for epoch in epoch_t:
        os.makedirs(epoch_dir(epoch))  # save time if not writeable

        samp = RandomSampler(all_imgs)
        batcher = BatchSampler(samp, batch_size=batch_size, drop_last=True)
        t = tqdm.tqdm(batcher, desc='Batch')
        for inds in t:
            opt.zero_grad()

            zs = z[make_var(np.asarray(inds))][:, :, newaxis, newaxis]
            recons = generator(zs)
            imgs = make_var(all_imgs[inds])
            loss = loss_fn(recons, imgs)

            loss.backward()
            opt.step()

            z_norms = torch.norm(
                torch.squeeze(torch.squeeze(zs, 3), 2), p=2, dim=1)
            which = z_norms > 1
            embiggen = (slice(None),) + (newaxis,) * 3
            zs[which[embiggen]] /= z_norms[embiggen]

            loss_val = loss.data.cpu().numpy()[0]
            t.set_postfix(loss=loss_val)

        epoch_t.write("Epoch {}: final loss {}".format(epoch, loss_val))
        z_numpy = z.data.cpu().numpy()
        mean = np.mean(z_numpy, axis=0)
        cov = np.cov(z_numpy, rowvar=False)

        d = partial(os.path.join, epoch_dir(epoch))
        torch.save(generator.state_dict(), d('gen-params.pkl'))
        np.save(d('latents.npy'), z_numpy)
        np.savez(d('latents-fit.npz'), mean=mean, cov=cov)

        latent_samps = samp_latent_stds.dot(np.linalg.cholesky(cov))
        latent_samps += mean
        latent_samps = latent_samps.astype(np.float32)
        img_samps = generator(make_var(latent_samps)[:, :, newaxis, newaxis])
        save_image(img_samps.data, d('samples.jpg'), nrow=10)
        np.save(d('samples-latents.npy'), latent_samps)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('data_dir')
    parser.add_argument('out_path', default='.', nargs='?')
    parser.add_argument('--epochs', type=int, default=50)
    parser.add_argument('--cuda', action='store_true', default=True)
    parser.add_argument('--no-cuda', action='store_false', dest='cuda')
    parser.add_argument('--batch-size', type=int, default=256)
    args = parser.parse_args()

    all_imgs, init_latents = load_data(args.data_dir)
    kwargs = vars(args)
    del kwargs['data_dir']
    train(all_imgs, init_latents, **kwargs)


if __name__ == '__main__':
    main()