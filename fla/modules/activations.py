# -*- coding: utf-8 -*-

# Copyright (c) 2023-2024, Tri Dao, Yu Zhang, Songlin Yang.

import torch
import torch.nn.functional as F

sigmoid_fwd_codestring = """
template <typename T> T sigmoid_fwd(T x) {
    return 1.0f / (1.0f + ::exp(-float(x)));
}
"""
sigmoid_bwd_codestring = """
template <typename T> T sigmoid_bwd(T x, T g) {
    float x_sigmoid = 1.0f / (1.0f + ::exp(-float(x)));
    return float(g) * x_sigmoid * (1.0f - x_sigmoid);
}
"""

sigmoid_fwd = torch.cuda.jiterator._create_jit_fn(sigmoid_fwd_codestring)
sigmoid_bwd = torch.cuda.jiterator._create_jit_fn(sigmoid_bwd_codestring)


class SigmoidFunction(torch.autograd.Function):

    @staticmethod
    def forward(ctx, x):
        ctx.save_for_backward(x)
        return sigmoid_fwd(x)

    @staticmethod
    def backward(ctx, dout):
        x, = ctx.saved_tensors
        return sigmoid_bwd(x, dout)


sigmoid = SigmoidFunction.apply

logsigmoid_fwd_codestring = """
template <typename T> T logsigmoid_fwd(T x) {
    return -::log(1.0f + ::exp(-float(x)));
}
"""
logsigmoid_bwd_codestring = """
template <typename T> T logsigmoid_bwd(T x, T g) {
    return float(g) * (1.0f - 1.0f / (1.0f + ::exp(-float(x))));
}
"""

logsigmoid_fwd = torch.cuda.jiterator._create_jit_fn(logsigmoid_fwd_codestring)
logsigmoid_bwd = torch.cuda.jiterator._create_jit_fn(logsigmoid_bwd_codestring)


class LogSigmoidFunction(torch.autograd.Function):

    @staticmethod
    def forward(ctx, x):
        ctx.save_for_backward(x)
        return logsigmoid_fwd(x)

    @staticmethod
    def backward(ctx, dout):
        x, = ctx.saved_tensors
        return logsigmoid_bwd(x, dout)


logsigmoid = LogSigmoidFunction.apply

swish_fwd_codestring = """
template <typename T> T swish_fwd(T x) {
    float x_sigmoid = 1.0f / (1.0f + ::exp(-float(x)));
    return float(x) * x_sigmoid;
}
"""
swish_bwd_codestring = """
template <typename T> T swish_bwd(T x, T g) {
    float x_sigmoid = 1.0f / (1.0f + ::exp(-float(x)));
    return float(g) * x_sigmoid * (1.0f - float(x) * x_sigmoid + float(x));
}
"""

swish_fwd = torch.cuda.jiterator._create_jit_fn(swish_fwd_codestring)
swish_bwd = torch.cuda.jiterator._create_jit_fn(swish_bwd_codestring)


class SwishFunction(torch.autograd.Function):

    @staticmethod
    def forward(ctx, x):
        ctx.save_for_backward(x)
        return swish_fwd(x)

    @staticmethod
    def backward(ctx, dout):
        x, = ctx.saved_tensors
        return swish_bwd(x, dout)


swish = SwishFunction.apply

# 1/sqrt(2*pi)-> 0.3989423
# 1/sqrt(2)   -> 0.70710678
# sqrt(2/pi)  -> 0.79788456


# this function is tanh approximation of gelu
# actual gelu is:
# x * 0.5 * (1.0 + torch.erf(x * 0.70710678))
@torch.jit.script
def bias_gelu(y, bias):
    x = bias + y
    return (x * 0.5 * (1.0 + torch.tanh(0.79788456 * x * (1 + 0.044715 * x * x)))).to(dtype=y.dtype)


# gradient of tanh approximation of gelu
# gradient of actual gelu is:
# 0.5 * (1. + torch.erf(x * 0.70710678)) + 0.3989423 * x * torch.exp(-0.5 * x * x)
@torch.jit.script
def bias_gelu_bwd(g, y, bias):
    """Assume that y has shape (B, D) and bias has shape (D)"""
    x = bias + y
    tanh_out = torch.tanh(0.79788456 * x * (1 + 0.044715 * x * x))
    # sqrt(2/pi) * 3 * 0.044715 -> 0.1070322243
    ff = 0.5 * x * ((1 - tanh_out * tanh_out) * (0.79788456 + 0.1070322243 * x * x)) + 0.5 * (
        1 + tanh_out
    )
    grad_y = ff * g
    return grad_y.to(dtype=y.dtype), grad_y.sum(dim=(0), dtype=bias.dtype)


class GeLUFunction(torch.autograd.Function):

    @staticmethod
    # bias is an optional argument
    def forward(ctx, input, bias):
        ctx.save_for_backward(input, bias)
        return bias_gelu(input, bias)

    @staticmethod
    def backward(ctx, grad_output):
        input, bias = ctx.saved_tensors
        tmp = bias_gelu_bwd(grad_output, input, bias)
        return tmp, tmp


bias_gelu_impl = GeLUFunction.apply


# this function is tanh approximation of gelu
# actual gelu is:
# x * 0.5 * (1.0 + torch.erf(x * 0.70710678))
@torch.jit.script
def gelu_fwd(x):
    return (x * 0.5 * (1.0 + torch.tanh(0.79788456 * x * (1 + 0.044715 * x * x)))).to(dtype=x.dtype)


# gradient of tanh approximation of gelu
# gradient of actual gelu is:
# 0.5 * (1. + torch.erf(x * 0.70710678)) + 0.3989423 * x * torch.exp(-0.5 * x * x)
@torch.jit.script
def gelu_bwd(g, x):
    tanh_out = torch.tanh(0.79788456 * x * (1 + 0.044715 * x * x))
    # sqrt(2/pi) * 3 * 0.044715 -> 0.1070322243
    ff = 0.5 * x * ((1 - tanh_out * tanh_out) * (0.79788456 + 0.1070322243 * x * x)) + 0.5 * (
        1 + tanh_out
    )
    return (ff * g).to(dtype=x.dtype)


class FastGeLUFunction(torch.autograd.Function):
    @staticmethod
    # bias is an optional argument
    def forward(ctx, input):
        ctx.save_for_backward(input)
        return gelu_fwd(input)

    @staticmethod
    def backward(ctx, grad_output):
        (input,) = ctx.saved_tensors
        tmp = gelu_bwd(grad_output, input)
        return tmp


fast_gelu_impl = FastGeLUFunction.apply


@torch.jit.script
def relu_bwd(g, x):
    return torch.where(x >= 0, g, 0.0).to(dtype=x.dtype)


@torch.jit.script
def sqrelu_fwd(x):
    r = F.relu(x)
    return (r * r).to(dtype=x.dtype)


@torch.jit.script
def sqrelu_bwd(g, x):
    return (2.0 * g * F.relu(x)).to(dtype=x.dtype)


class SquaredReLUFunction(torch.autograd.Function):

    @staticmethod
    def forward(ctx, input):
        ctx.save_for_backward(input)
        return sqrelu_fwd(input)

    @staticmethod
    def backward(ctx, grad_output):
        input, = ctx.saved_tensors
        return sqrelu_bwd(grad_output, input)


sqrelu = SquaredReLUFunction.apply


swiglu_fwd_codestring = """
template <typename T> T swiglu_fwd(T x, T y) {
    return float(x) * float(y) / (1.0f + ::exp(-float(x)));
}
"""
swiglu_bwd_codestring = """
template <typename T> T swiglu_bwd(T x, T y, T g, T& dx, T& dy) {
    float x_sigmoid = 1.0f / (1.0f + ::exp(-float(x)));
    dx = x_sigmoid * (1 + float(x) * (1.0f - x_sigmoid)) * float(g) * float(y);
    dy = float(x) * x_sigmoid * float(g);
}
"""

swiglu_bwd_with_output_codestring = """
template <typename T> T swiglu_bwd_with_output(T x, T y, T g, T& dx, T& dy, T& z) {
    float x_sigmoid = 1.0f / (1.0f + ::exp(-float(x)));
    float x_swish = float(x) * x_sigmoid;
    dx = x_sigmoid * (1 + float(x) * (1.0f - x_sigmoid)) * float(g) * float(y);
    dy = x_swish * float(g);
    z = x_swish * float(y);
}
"""

swiglu_fwd = torch.cuda.jiterator._create_jit_fn(swiglu_fwd_codestring)
swiglu_bwd = torch.cuda.jiterator._create_multi_output_jit_fn(swiglu_bwd_codestring, num_outputs=2)
swiglu_bwd_with_output = torch.cuda.jiterator._create_multi_output_jit_fn(swiglu_bwd_with_output_codestring, num_outputs=3)


class SwiGLUFunction(torch.autograd.Function):
    r"""
    Swish-Gated Linear Unit (SwiGLU) function.

    .. math::
        \text{SwiGLU}(x, y) = swish(x) * y = \frac{x}{1 + \exp(-x)} * y
    """

    @staticmethod
    def forward(ctx, x, y):
        ctx.save_for_backward(x, y)
        return swiglu_fwd(x, y)

    @staticmethod
    def backward(ctx, dout):
        x, y = ctx.saved_tensors
        return swiglu_bwd(x, y, dout)


class SwiGLULinearFunction(torch.autograd.Function):
    r"""
    Swish-Gated Linear Unit (SwiGLU) function followed by a linear transformation.

    .. math::
        \text{SwiGLULinear}(x, y, W, b) = (swish(x) * y) W + b

    This simple wrap discards the intermediate results of SwiGLU(x, y) to save memory.
    """

    @staticmethod
    def forward(ctx, x, y, weight, bias):
        z = swiglu_fwd(x, y)
        out = F.linear(z.to(weight.dtype), weight, bias)
        # We don't store z, will be recomputed in the backward pass to save memory
        ctx.save_for_backward(x, y, weight)
        ctx.linear_bias_is_none = bias is None
        return out

    @staticmethod
    def backward(ctx, dout, *args):
        x, y, weight = ctx.saved_tensors
        dout = dout.reshape(-1, dout.shape[-1])
        dz = F.linear(dout, weight.t()).view_as(x)
        dx, dy, z = swiglu_bwd_with_output(x, y, dz)
        dlinear_weight = torch.einsum("bo,bi->oi", dout, z.reshape(-1, z.shape[-1]))
        dlinear_bias = None if ctx.linear_bias_is_none else dout.sum(0)
        return dx, dy, dlinear_weight, dlinear_bias


swiglu = SwiGLUFunction.apply

swiglu_linear = SwiGLULinearFunction.apply

ACT2FN = {
    'sigmoid': sigmoid,
    'logsigmoid': logsigmoid,
    'silu': swish,
    'swish': swish,
    'sqrelu': sqrelu,
    'gelu': fast_gelu_impl,
    'bias_gelu': bias_gelu_impl,
}
