import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
from torch.nn import init

from layers import GaussianSample, GaussianMerge, GumbelSoftmax
from inference import log_gaussian, log_standard_gaussian, gaussian_entropy, log_marginal_gaussian


class Perceptron(nn.Module):
    def __init__(self, dims, activation_fn=F.relu, output_activation=None):
        super(Perceptron, self).__init__()
        self.dims = dims
        self.activation_fn = activation_fn
        self.output_activation = output_activation

        self.layers = nn.ModuleList(list(map(lambda d: nn.Linear(*d), list(zip(dims, dims[1:])))))

    def forward(self, x):
        for i, layer in enumerate(self.layers):
            x = layer(x)
            if i == len(self.layers)-1 and self.output_activation is not None:
                x = self.output_activation(x)
            else:
                x = self.activation_fn(x)

        return x



class Encoder(nn.Module):
    def __init__(self, dims, sample_layer=GaussianSample, activation_fn=nn.ReLU, batch_norm=True):
        """
        Inference network

        Attempts to infer the probability distribution
        p(z|x) from the data by fitting a variational
        distribution q_φ(z|x). Returns the two parameters
        of the distribution (µ, log σ²).

        :param dims: dimensions of the networks
           given by the number of neurons on the form
           [input_dim, [hidden_dims], latent_dim].
        """
        super(Encoder, self).__init__()

        [x_dim, h_dim, z_dim] = dims

        if isinstance(x_dim, list):
            self.first_dense = nn.ModuleList(
                [nn.Linear(in_dim, h_dim[0]) for in_dim in x_dim])
        else:
            self.first_dense = nn.ModuleList([nn.Linear(x_dim, h_dim[0])])

        # Combine all inputs in the first layer by summation
        # e.g. z = w_1 * x + w_2 * a
        linear_layers = []
        for idx in range(0, len(h_dim) - 1):
            if batch_norm:
                linear_layers += [
                    activation_fn(),
                    nn.BatchNorm1d(h_dim[idx]),
                    nn.Linear(h_dim[idx], h_dim[idx+1])
                ]
            else:
                linear_layers += [
                    activation_fn(),
                    nn.Linear(h_dim[idx], h_dim[idx+1])
                ]

        linear_layers += [activation_fn()]
        if batch_norm:
            linear_layers += [nn.BatchNorm1d(h_dim[-1])]

        self.hidden = nn.ModuleList(linear_layers)
        self.sample = sample_layer(h_dim[-1], z_dim)

    def forward(self, input_):
        if not isinstance(input_, list):
            input_ = [input_]

        multi_x = []
        for x, dense in zip(input_, self.first_dense):
            multi_x += [dense(x)]
        x = sum(multi_x)

        for layer in self.hidden:
            x = layer(x)
        return self.sample(x)


class Decoder(nn.Module):
    def __init__(self, dims, output_activation=F.sigmoid, activation_fn=nn.ReLU, batch_norm=True):
        """
        Generative network

        Generates samples from the original distribution
        p(x) by transforming a latent representation, e.g.
        by finding p_θ(x|z).

        :param dims: dimensions of the networks
            given by the number of neurons on the form
            [latent_dim, [hidden_dims], input_dim].
        """
        super(Decoder, self).__init__()

        [z_dim, h_dim, x_dim] = dims

        if isinstance(z_dim, list):
            self.first_dense = nn.ModuleList(
                [nn.Linear(in_dim, h_dim[0]) for in_dim in z_dim])
        else:
            self.first_dense = nn.ModuleList([nn.Linear(z_dim, h_dim[0])])

        # Combine all inputs in the first layer by summation
        # e.g. z = w_1 * x + w_2 * a
        linear_layers = []
        for idx in range(0, len(h_dim) - 1):
            if batch_norm:
                linear_layers += [
                    activation_fn(),
                    nn.BatchNorm1d(h_dim[idx]),
                    nn.Linear(h_dim[idx], h_dim[idx+1])
                ]
            else:
                linear_layers += [
                    activation_fn(),
                    nn.Linear(h_dim[idx], h_dim[idx+1])
                ]

        linear_layers += [activation_fn()]
        if batch_norm:
            linear_layers += [nn.BatchNorm1d(h_dim[-1])]

        self.hidden = nn.ModuleList(linear_layers)

        self.reconstruction = nn.Linear(h_dim[-1], x_dim)
        self.output_activation = output_activation


    def forward(self, input_):
        if not isinstance(input_, list):
            input_ = [input_]

        multi_x = []
        for x, dense in zip(input_, self.first_dense):
            multi_x += [dense(x)]
        x = sum(multi_x)

        for layer in self.hidden:
            x = layer(x)

        if self.output_activation is not None:
            return self.output_activation(self.reconstruction(x))
        else:
            return self.reconstruction(x)


class ConvPreEncoder(nn.Module):
    def __init__(self): 
        super(ConvPreEncoder, self).__init__()
        kernel_size = 4
        self.conv_layers = nn.Sequential(
            nn.Conv2d(3, 32, kernel_size, stride=2, padding=1),
            nn.ReLU(),
            nn.Conv2d(32, 32, kernel_size, stride=2, padding=1),
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size, stride=2, padding=1),
            nn.ReLU(),
            nn.Conv2d(64, 64, kernel_size, stride=2, padding=1),
        )

    def forward(self, x):
        x = self.conv_layers(x)
        return x.view(x.shape[0], -1)

class ConvPostDecoder(nn.Module):
    def __init__(self): 
        super(ConvPostDecoder, self).__init__()
        kernel_size = 4
        self.conv_layers = nn.Sequential(
            nn.ConvTranspose2d(64, 64, kernel_size, stride=2, padding=1),
            nn.ReLU(),
            nn.ConvTranspose2d(64, 32, kernel_size, stride=2, padding=1),
            nn.ReLU(),
            nn.ConvTranspose2d(32, 32, kernel_size, stride=2, padding=1),
            nn.ReLU(),
            nn.ConvTranspose2d(32, 3, kernel_size, stride=2, padding=1),
            nn.Sigmoid()
        )

    def forward(self, x):
        x = x.view(x.shape[0], 64, 4, 4)
        return self.conv_layers(x)

    


class VariationalAutoencoder(nn.Module):
    def __init__(self, dims, conv=False, activation_fn=nn.ReLU, batch_norm=True):
        """
        Variational Autoencoder [Kingma 2013] model
        consisting of an encoder/decoder pair for which
        a variational distribution is fitted to the
        encoder. Also known as the M1 model in [Kingma 2014].

        :param dims: x, z and hidden dimensions of the networks
        """
        super(VariationalAutoencoder, self).__init__()

        [x_dim, z_dim, h_dim] = dims
        self.z_dim = z_dim
        self.flow = None

        self.conv = conv

        if conv:
            self.pre_encoder = ConvPreEncoder()
            self.post_decoder = ConvPostDecoder()
            x_dim = 64 * 4 * 4

        self.encoder = Encoder(
            [x_dim, h_dim, z_dim],
            activation_fn=activation_fn,
            batch_norm=batch_norm
        )
        self.decoder = Decoder(
            [z_dim, list(reversed(h_dim)), x_dim],
            activation_fn=activation_fn,
            batch_norm=batch_norm
        )

        self.kl_divergence = 0

        for m in self.modules():
            if isinstance(m, nn.Linear):
                init.xavier_normal(m.weight.data)
                if m.bias is not None:
                    m.bias.data.zero_()

    def _kld(self, z, q_param, p_param=None):
        """
        Computes the KL-divergence of
        some element z.
        KL(q||p) = -∫ q(z) log [ p(z) / q(z) ]
                 = -E[log p(z) - log q(z)]
        :param z: sample from q-distribuion
        :param q_param: (mu, log_var) of the q-distribution
        :param p_param: (mu, log_var) of the p-distribution
        :return: KL(q||p)
        """
        (mu, log_var) = q_param

        if self.flow is not None:
            f_z, log_det_z = self.flow(z)
            qz = log_gaussian(z, mu, log_var) - sum(log_det_z)
            z = f_z
        else:
            qz = log_gaussian(z, mu, log_var)

        if p_param is None:
            pz = log_standard_gaussian(z)
        else:
            (mu, log_var) = p_param
            pz = log_gaussian(z, mu, log_var)

        kl = qz - pz

        return kl

    def add_flow(self, flow):
        self.flow = flow

    def forward(self, x, y=None):
        """
        Runs a data point through the model in order
        to provide its reconstruction and q distribution
        parameters.

        :param x: input data
        :return: reconstructed input
        """
        
        if self.conv:
            x = self.pre_encoder(x)
        z, z_mu, z_log_var = self.encoder(x)

        # Analytical, following https://github.com/dpkingma/nips14-ssl
        self.kl_divergence = gaussian_entropy(z_mu, z_log_var) - log_marginal_gaussian(z_mu, z_log_var)

        x_mu = self.decoder(z)

        if self.conv: 
            x_mu = self.post_decoder(x_mu)

        return x_mu

    def sample(self, z):
        """
        Given z ~ N(0, I) generates a sample from
        the learned distribution based on p_θ(x|z).
        :param z: (torch.autograd.Variable) Random normal variable
        :return: (torch.autograd.Variable) generated sample
        """
        if self.conv:
            return self.post_decoder(self.decoder(z))
        else:
            return self.decoder(z)

    def encode(self, x):
        if self.conv:
            return self.encoder(self.pre_encoder(x))
        else:
            return self.encoder(x)


# (MACIEK): we do not use the classes defined below.
class GumbelAutoencoder(nn.Module):
    def __init__(self, dims, n_samples=100):
        super(GumbelAutoencoder, self).__init__()

        [x_dim, z_dim, h_dim] = dims
        self.z_dim = z_dim
        self.n_samples = n_samples

        self.encoder = Perceptron([x_dim, *h_dim])
        self.sampler = GumbelSoftmax(h_dim[-1], z_dim, n_samples)
        self.decoder = Perceptron([z_dim, *reversed(h_dim), x_dim], output_activation=F.sigmoid)

        self.kl_divergence = 0

        for m in self.modules():
            if isinstance(m, nn.Linear):
                init.xavier_normal(m.weight.data)
                if m.bias is not None:
                    m.bias.data.zero_()

    def _kld(self, qz):
        k = Variable(torch.FloatTensor([self.z_dim]), requires_grad=False)
        kl = qz * (torch.log(qz + 1e-8) - torch.log(1.0/k))
        kl = kl.view(-1, self.n_samples, self.z_dim)
        return torch.sum(torch.sum(kl, dim=1), dim=1)

    def forward(self, x, y=None, tau=1):
        x = self.encoder(x)

        sample, qz = self.sampler(x, tau)
        self.kl_divergence = self._kld(qz)

        x_mu = self.decoder(sample)

        return x_mu

    def sample(self, z):
        return self.decoder(z)


class LadderEncoder(nn.Module):
    def __init__(self, dims):
        """
        The ladder encoder differs from the standard encoder
        by using batch-normalization and LReLU activation.
        Additionally, it also returns the transformation x.

        :param dims: dimensions [input_dim, [hidden_dims], [latent_dims]].
        """
        super(LadderEncoder, self).__init__()
        [x_dim, h_dim, self.z_dim] = dims
        self.in_features = x_dim
        self.out_features = h_dim

        self.linear = nn.Linear(x_dim, h_dim)
        self.batchnorm = nn.BatchNorm1d(h_dim)
        self.sample = GaussianSample(h_dim, self.z_dim)

    def forward(self, x):
        x = self.linear(x)
        x = F.leaky_relu(self.batchnorm(x), 0.1)
        return x, self.sample(x)


class LadderDecoder(nn.Module):
    def __init__(self, dims):
        """
        The ladder dencoder differs from the standard encoder
        by using batch-normalization and LReLU activation.
        Additionally, it also returns the transformation x.

        :param dims: dimensions of the networks
            given by the number of neurons on the form
            [latent_dim, [hidden_dims], input_dim].
        """
        super(LadderDecoder, self).__init__()

        [self.z_dim, h_dim, x_dim] = dims

        self.linear1 = nn.Linear(x_dim, h_dim)
        self.batchnorm1 = nn.BatchNorm1d(h_dim)
        self.merge = GaussianMerge(h_dim, self.z_dim)

        self.linear2 = nn.Linear(x_dim, h_dim)
        self.batchnorm2 = nn.BatchNorm1d(h_dim)
        self.sample = GaussianSample(h_dim, self.z_dim)

    def forward(self, x, l_mu=None, l_log_var=None):
        if l_mu is not None:
            # Sample from this encoder layer and merge
            z = self.linear1(x)
            z = F.leaky_relu(self.batchnorm1(z), 0.1)
            q_z, q_mu, q_log_var = self.merge(z, l_mu, l_log_var)

        # Sample from the decoder and send forward
        z = self.linear2(x)
        z = F.leaky_relu(self.batchnorm2(z), 0.1)
        z, p_mu, p_log_var = self.sample(z)

        if l_mu is None:
            return z

        return z, (q_z, (q_mu, q_log_var), (p_mu, p_log_var))


class LadderVariationalAutoencoder(VariationalAutoencoder):
    def __init__(self, dims):
        """
        Ladder Variational Autoencoder as described by
        [Sønderby 2016]. Adds several stochastic
        layers to improve the log-likelihood estimate.

        :param dims: x, z and hidden dimensions of the networks
        """
        [x_dim, z_dim, h_dim] = dims
        super(LadderVariationalAutoencoder, self).__init__([x_dim, z_dim[0], h_dim])

        neurons = [x_dim, *h_dim]
        encoder_layers = [LadderEncoder([neurons[i - 1], neurons[i], z_dim[i - 1]]) for i in range(1, len(neurons))]
        decoder_layers = [LadderDecoder([z_dim[i - 1], h_dim[i - 1], z_dim[i]]) for i in range(1, len(h_dim))][::-1]

        self.encoder = nn.ModuleList(encoder_layers)
        self.decoder = nn.ModuleList(decoder_layers)
        self.reconstruction = Decoder([z_dim[0], h_dim, x_dim])

        for m in self.modules():
            if isinstance(m, nn.Linear):
                init.xavier_normal(m.weight.data)
                if m.bias is not None:
                    m.bias.data.zero_()

    def forward(self, x):
        # Gather latent representation
        # from encoders along with final z.
        latents = []
        for encoder in self.encoder:
            x, (z, mu, log_var) = encoder(x)
            latents.append((mu, log_var))

        latents = list(reversed(latents))

        self.kl_divergence = 0
        for i, decoder in enumerate([-1, *self.decoder]):
            # If at top, encoder == decoder,
            # use prior for KL.
            l_mu, l_log_var = latents[i]
            if i == 0:
                self.kl_divergence += self._kld(z, (l_mu, l_log_var))

            # Perform downword merge of information.
            else:
                z, kl = decoder(z, l_mu, l_log_var)
                self.kl_divergence += self._kld(*kl)

        x_mu = self.reconstruction(z)
        return x_mu

    def sample(self, z):
        for decoder in self.decoder:
            z = decoder(z)
        return self.reconstruction(z)

