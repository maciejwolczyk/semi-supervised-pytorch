import math
import numpy as np
import torch
import torch.nn.functional as F


def log_standard_gaussian(x):
    """
    Evaluates the log pdf of a standard normal distribution at x.

    :param x: point to evaluate
    :return: log N(x|0,I)
    """
    return torch.sum(-0.5 * math.log(2 * math.pi) - x ** 2 / 2, dim=-1)


def log_gaussian(x, mu, log_var):
    """
    Returns the log pdf of a normal distribution parametrised
    by mu and log_var evaluated at x.

    :param x: point to evaluate
    :param mu: mean of distribution
    :param log_var: log variance of distribution
    :return: log N(x|µ,σ)
    """
    log_pdf = - 0.5 * np.log(2 * math.pi) - log_var / 2 - (x - mu)**2 / (2 * torch.exp(log_var))
    return torch.sum(log_pdf, dim=-1)


def gaussian_entropy(mu, log_var):
    """
    The following entropy calculate analytically:
    -log E_{N(µ, σ)} [ N(µ, σ) ]
    """

    entropy = -0.5 * (np.log(2 * math.pi) + 1 + log_var)
    entropy = torch.sum(entropy, dim=-1)
    return entropy


def log_marginal_gaussian(mu, log_var):
    """
    The following expectaction calculated analytically:
    log E_{x ~ N(µ, σ)} [  N(x | 0, 1) ] 
    """
    log_marginal = -0.5 * (np.log(2 * math.pi) + (mu ** 2 + torch.exp(log_var)))
    log_marginal = torch.sum(log_marginal, dim=-1)
    return log_marginal

def log_standard_categorical(p):
    """
    Calculates the cross entropy between a (one-hot) categorical vector
    and a standard (uniform) categorical distribution.

    :param p: one-hot categorical distribution
    :return: H(p, u)
    """
    # Uniform prior over y
    prior = F.softmax(torch.ones_like(p), dim=1)
    prior.requires_grad = False

    cross_entropy = -torch.sum(p * torch.log(prior + 1e-8), dim=1)

    return cross_entropy
