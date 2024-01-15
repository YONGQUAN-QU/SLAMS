r"""Score modules"""

import math
from zuko.utils import broadcast
import torch
import torch.nn as nn

from torch import Size, Tensor
from tqdm import tqdm
from typing import *

from .nn import *


class TimeEmbedding(nn.Sequential):
    r"""Creates a time embedding.

    Arguments:
        features: The number of embedding features.
    """

    def __init__(self, features: int):
        super().__init__(
            nn.Linear(32, 256),
            nn.SiLU(),
            nn.Linear(256, features),
        )

        self.register_buffer('freqs', torch.pi * torch.arange(1, 16 + 1))

    def forward(self, t: Tensor) -> Tensor:
        t = self.freqs * t.unsqueeze(dim=-1)
        t = torch.cat((t.cos(), t.sin()), dim=-1)

        return super().forward(t)


class ScoreNet(nn.Module):
    r"""Creates a score network.

    Arguments:
        features: The number of features.
        context: The number of context features.
        embedding: The number of time embedding features.
    """

    def __init__(self, features: int, context: int = 0, embedding: int = 16, **kwargs):
        super().__init__()

        self.embedding = TimeEmbedding(embedding)
        self.network = ResMLP(features + context + embedding, features, **kwargs)

    def forward(self, x: Tensor, t: Tensor, c: Tensor = None) -> Tensor:
        t = self.embedding(t)

        if c is None:
            x, t = broadcast(x, t, ignore=1)
            x = torch.cat((x, t), dim=-1)
        else:
            x, t, c = broadcast(x, t, c, ignore=1)
            x = torch.cat((x, t, c), dim=-1)
            
        return self.network(x)
    
    
class ScoreUNet(nn.Module):
    r"""Creates a U-Net score network.

    Arguments:
        channels: The number of channels.
        context: The number of context channels.
        embedding: The number of time embedding features.
    """

    def __init__(self, channels: int, context: int = 0, embedding: int = 64, **kwargs):
        super().__init__()

        self.embedding = TimeEmbedding(embedding)
        self.network = UNet(channels + context, channels, embedding, **kwargs)

    def forward(self, x: Tensor, t: Tensor, c: Tensor = None) -> Tensor:
        dims = self.network.spatial + 1

        if c is None:
            y = x
        else:
            y = torch.cat(broadcast(x, c, ignore=dims), dim=-dims)

        y = y.reshape(-1, *y.shape[-dims:])
        t = t.reshape(-1)
        t = self.embedding(t)

        return self.network(y, t).reshape(x.shape)


class MCScoreNet(nn.Module):
    r"""Creates a score network for a Markov chain.

    Arguments:
        features: The number of features.
        context: The number of context features.
        order: The order of the Markov chain.
    """

    def __init__(self, features: int, context: int = 0, order: int = 1, **kwargs):
        super().__init__()

        self.order = order

        if kwargs.get('spatial', 0) > 0:
            build = ScoreUNet
        else:
            build = ScoreNet
            
        self.kernel = build(features * (2 * order + 1), context, **kwargs)

    def forward(
        self,
        x: Tensor,  # (B, L, C, H, W)
        t: Tensor,  # ()
        c: Tensor = None,  # (C', H, W)
    ) -> Tensor:
        
        x = self.unfold(x, self.order)
        s = self.kernel(x, t, c)
        s = self.fold(s, self.order)
        
        return s

    @staticmethod
    @torch.jit.script_if_tracing
    def unfold(x: Tensor, order: int) -> Tensor:
        x = x.unfold(1, 2 * order + 1, 1)
        x = x.movedim(-1, 2)
        x = x.flatten(2, 3)

        return x

    @staticmethod
    @torch.jit.script_if_tracing
    def fold(x: Tensor, order: int) -> Tensor:
        x = x.unflatten(2, (2 * order  + 1, -1))

        return torch.cat((
            x[:, 0, :order],
            x[:, :, order],
            x[:, -1, -order:],
        ), dim=1)


class LocalScoreUNet(ScoreUNet):
    r"""Creates a score U-Net with a forcing channel."""

    def __init__(
        self,
        channels: int,
        size = 64,
        with_forcing = True,
        **kwargs,
    ):
        if with_forcing:
            context_channel = 1 
            domain = 2 * torch.pi / size * (torch.arange(size) + 1 / 2)
            forcing = torch.sin(4 * domain).expand(1, size, size).clone()
            
        else:
            context_channel = 0
            forcing = None
        
        super().__init__(channels, context_channel, **kwargs)
        self.register_buffer('forcing', forcing)

    def forward(self, x: Tensor, t: Tensor, c: Tensor = None) -> Tensor:
        return super().forward(x, t, self.forcing)
    

class VPSDE(nn.Module):
    r"""Creates a noise scheduler for the variance preserving (VP) SDE.

    .. math::
        \mu(t) & = \alpha(t) \\
        \sigma(t)^2 & = 1 - \alpha(t)^2 + \eta^2

    Arguments:
        eps: A noise estimator :math:`\epsilon_\phi(x, t)`.
        shape: The event shape.
        alpha: The choice of :math:`\alpha(t)`.
        eta: A numerical stability term.
    """

    def __init__(
        self,
        eps: nn.Module,
        shape: Size,
        alpha: str = 'cos',
        eta: float = 1e-3,
    ):
        super().__init__()

        self.eps = eps
        self.shape = shape
        self.dims = tuple(range(-len(shape), 0))
        self.eta = eta

        if alpha == 'lin':
            self.alpha = lambda t: 1 - (1 - eta) * t
        elif alpha == 'cos':
            self.alpha = lambda t: torch.cos(math.acos(math.sqrt(eta)) * t) ** 2
        elif alpha == 'exp':
            self.alpha = lambda t: torch.exp(math.log(eta) * t**2)
        else:
            raise ValueError()

        self.register_buffer('device', torch.empty(()))

    def mu(self, t: Tensor) -> Tensor:
        return self.alpha(t)

    def sigma(self, t: Tensor) -> Tensor:
        return (1 - self.alpha(t) ** 2 + self.eta ** 2).sqrt()

    def forward(self, x: Tensor, t: Tensor, train: bool = False) -> Tensor:
        r"""Samples from the perturbation kernel :math:`p(x(t) | x)`."""

        t = t.reshape(t.shape + (1,) * len(self.shape))

        eps = torch.randn_like(x)
        x = self.mu(t) * x + self.sigma(t) * eps

        if train:
            return x, eps
        else:
            return x

    def sample(
        self,
        shape: Size = (),
        c: Tensor = None,
        steps: int = 64,
        corrections: int = 0,
        tau: float = 1.0,
    ) -> Tensor:
        r"""Samples from :math:`p(x(0))`.

        Arguments:
            shape: The batch shape.
            c: The optional context.
            steps: The number of discrete time steps.
            corrections: The number of Langevin corrections per time steps.
            tau: The amplitude of Langevin steps.
        """

        x = torch.randn(shape + self.shape).to(self.device)
        x = x.reshape(-1, *self.shape)

        time = torch.linspace(1, 0, steps + 1).to(self.device)
        dt = 1 / steps

        with torch.no_grad():
            for t in tqdm(time[:-1]):
                # Predictor
                r = self.mu(t - dt) / self.mu(t)
                x = r * x + (self.sigma(t - dt) - r * self.sigma(t)) * self.eps(x, t, c)

                # Corrector
                for _ in range(corrections):
                    eps = torch.randn_like(x)
                    s = -self.eps(x, t - dt, c) / self.sigma(t - dt)
                    delta = tau / s.square().mean(dim=self.dims, keepdim=True)

                    x = x + delta * s + torch.sqrt(2 * delta) * eps

        return x.reshape(shape + self.shape)

    def loss(self, x: Tensor, c: Tensor = None, w: Tensor = None) -> Tensor:
        r"""Returns the denoising loss."""

        t = torch.rand(x.shape[0], dtype=x.dtype, device=x.device)
        x, eps = self.forward(x, t, train=True)

        err = (self.eps(x, t, c) - eps).square()

        if w is None:
            return err.mean()
        else:
            return (err * w).mean() / w.mean()

        
class GaussianScore(nn.Module):
    r"""Creates a score module for Gaussian inverse problems.

    .. math:: p(y | x) = N(y | A(x), Σ)

    Note:
        This module returns :math:`-\sigma(t) s(x(t), t | y)`.
    """

    def __init__(
        self,
        y: Tensor,
        A: Callable[[Tensor], Tensor],
        std: Union[float, Tensor],
        sde: VPSDE,
        gamma: Union[float, Tensor] = 1e-2,
        detach: bool = False,
        corrected_log: bool = False
    ):
        super().__init__()

        self.register_buffer('y', y)
        self.register_buffer('std', torch.as_tensor(std))
        self.register_buffer('gamma', torch.as_tensor(gamma))

        self.A = A
        self.sde = sde
        self.detach = detach
        self.corrected_log = corrected_log

    def forward(self, x: Tensor, t: Tensor, c: Tensor = None) -> Tensor:
        mu, sigma = self.sde.mu(t), self.sde.sigma(t)

        if self.detach:
            eps = self.sde.eps(x, t, c)

        with torch.enable_grad():
            x = x.detach().requires_grad_(True)

            if not self.detach:
                eps = self.sde.eps(x, t, c)
                
            x_ = (x - sigma * eps) / mu # denoised_x
            
            err = self.y - self.A(x_)
            var = self.std ** 2 + self.gamma * (sigma / mu) ** 2
            
            if self.corrected_log:
                yt_mean, yt_var = self.y.mean(axis=0), self.y.var(axis=0)
                x_mean = x_.mean(axis=0)
                
                log_p = -((err ** 2 / var).sum() + ((yt_mean[-1] - x_mean[-1]) ** 2 / yt_var[-1]).sum()) / 2 
                
            else:
                log_p = -(err ** 2 / var).sum() / 2

        s, = torch.autograd.grad(log_p, x)

        return eps - sigma * s
