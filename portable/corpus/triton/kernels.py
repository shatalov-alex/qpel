"""Triton kernels for qpel's Triton frontend (frontends/triton/README.md).

Written for qpel in the style of the Triton tutorials (vector addition, fused softmax, layer
norm; https://triton-lang.org/main/getting-started/tutorials/, MIT): one program per block or
row, masked loads and stores, reductions over a block, loops over a row in blocks.
"""
import triton
import triton.language as tl
from triton.language.extra import libdevice


@triton.jit
def add_kernel(x_ptr, y_ptr, output_ptr, n_elements, BLOCK_SIZE: tl.constexpr):
    pid = tl.program_id(axis=0)
    offsets = pid * BLOCK_SIZE + tl.arange(0, BLOCK_SIZE)
    mask = offsets < n_elements
    x = tl.load(x_ptr + offsets, mask=mask)
    y = tl.load(y_ptr + offsets, mask=mask)
    tl.store(output_ptr + offsets, x + y, mask=mask)


@triton.jit
def scale_shift_kernel(x_ptr, out_ptr, n_elements, scale, shift, BLOCK_SIZE: tl.constexpr):
    pid = tl.program_id(0)
    offsets = pid * BLOCK_SIZE + tl.arange(0, BLOCK_SIZE)
    mask = offsets < n_elements
    x = tl.load(x_ptr + offsets, mask=mask, other=0.0)
    y = tl.where(x > 0, x * scale + shift, -x)
    tl.store(out_ptr + offsets, y, mask=mask)


@triton.jit
def softmax_kernel(output_ptr, input_ptr, input_row_stride, output_row_stride, n_cols, BLOCK_SIZE: tl.constexpr):
    row_idx = tl.program_id(0)
    col_offsets = tl.arange(0, BLOCK_SIZE)
    mask = col_offsets < n_cols
    row = tl.load(input_ptr + row_idx * input_row_stride + col_offsets, mask=mask, other=-float('inf'))
    row_minus_max = row - tl.max(row, axis=0)
    numerator = tl.exp(row_minus_max)
    denominator = tl.sum(numerator, axis=0)
    tl.store(output_ptr + row_idx * output_row_stride + col_offsets, numerator / denominator, mask=mask)


@triton.jit
def copy2d_kernel(src_ptr, dst_ptr, M, N, stride_src, stride_dst, BLOCK_M: tl.constexpr, BLOCK_N: tl.constexpr):
    pid_m = tl.program_id(0)
    pid_n = tl.program_id(1)
    offs_m = pid_m * BLOCK_M + tl.arange(0, BLOCK_M)
    offs_n = pid_n * BLOCK_N + tl.arange(0, BLOCK_N)
    mask = (offs_m[:, None] < M) & (offs_n[None, :] < N)
    tile = tl.load(src_ptr + offs_m[:, None] * stride_src + offs_n[None, :], mask=mask)
    tl.store(dst_ptr + offs_n[None, :] * stride_dst + offs_m[:, None], tile * 2.0, mask=mask)


@triton.jit
def layer_norm_kernel(X, Y, W, B, stride, N, eps, BLOCK_SIZE: tl.constexpr):
    row = tl.program_id(0)
    Y += row * stride
    X += row * stride
    _mean = tl.zeros([BLOCK_SIZE], dtype=tl.float32)
    for off in range(0, N, BLOCK_SIZE):
        cols = off + tl.arange(0, BLOCK_SIZE)
        a = tl.load(X + cols, mask=cols < N, other=0.).to(tl.float32)
        _mean += a
    mean = tl.sum(_mean, axis=0) / N
    _var = tl.zeros([BLOCK_SIZE], dtype=tl.float32)
    for off in range(0, N, BLOCK_SIZE):
        cols = off + tl.arange(0, BLOCK_SIZE)
        x = tl.load(X + cols, mask=cols < N, other=0.).to(tl.float32)
        x = tl.where(cols < N, x - mean, 0.)
        _var += x * x
    var = tl.sum(_var, axis=0) / N
    rstd = 1 / tl.sqrt(var + eps)
    for off in range(0, N, BLOCK_SIZE):
        cols = off + tl.arange(0, BLOCK_SIZE)
        mask = cols < N
        w = tl.load(W + cols, mask=mask)
        b = tl.load(B + cols, mask=mask)
        x = tl.load(X + cols, mask=mask, other=0.).to(tl.float32)
        x_hat = (x - mean) * rstd
        tl.store(Y + cols, x_hat * w + b, mask=mask)


@triton.jit
def matmul_kernel(a_ptr, b_ptr, c_ptr, M, N, K, stride_am, stride_ak, stride_bk, stride_bn, stride_cm, stride_cn,
                  BLOCK_M: tl.constexpr, BLOCK_N: tl.constexpr, BLOCK_K: tl.constexpr, GROUP_M: tl.constexpr):
    pid = tl.program_id(axis=0)
    num_pid_m = tl.cdiv(M, BLOCK_M)
    num_pid_n = tl.cdiv(N, BLOCK_N)
    num_pid_in_group = GROUP_M * num_pid_n
    group_id = pid // num_pid_in_group
    first_pid_m = group_id * GROUP_M
    group_size_m = min(num_pid_m - first_pid_m, GROUP_M)
    pid_m = first_pid_m + ((pid % num_pid_in_group) % group_size_m)
    pid_n = (pid % num_pid_in_group) // group_size_m
    offs_am = (pid_m * BLOCK_M + tl.arange(0, BLOCK_M)) % M
    offs_bn = (pid_n * BLOCK_N + tl.arange(0, BLOCK_N)) % N
    offs_k = tl.arange(0, BLOCK_K)
    a_ptrs = a_ptr + (offs_am[:, None] * stride_am + offs_k[None, :] * stride_ak)
    b_ptrs = b_ptr + (offs_k[:, None] * stride_bk + offs_bn[None, :] * stride_bn)
    accumulator = tl.zeros((BLOCK_M, BLOCK_N), dtype=tl.float32)
    for k in range(0, tl.cdiv(K, BLOCK_K)):
        a = tl.load(a_ptrs, mask=offs_k[None, :] < K - k * BLOCK_K, other=0.0)
        b = tl.load(b_ptrs, mask=offs_k[:, None] < K - k * BLOCK_K, other=0.0)
        accumulator = tl.dot(a, b, accumulator, input_precision="ieee")
        a_ptrs += BLOCK_K * stride_ak
        b_ptrs += BLOCK_K * stride_bk
    offs_cm = pid_m * BLOCK_M + tl.arange(0, BLOCK_M)
    offs_cn = pid_n * BLOCK_N + tl.arange(0, BLOCK_N)
    c_ptrs = c_ptr + stride_cm * offs_cm[:, None] + stride_cn * offs_cn[None, :]
    c_mask = (offs_cm[:, None] < M) & (offs_cn[None, :] < N)
    tl.store(c_ptrs, accumulator, mask=c_mask)


@triton.jit
def row_stats_kernel(x_ptr, max_ptr, mean_ptr, M, N, stride, BLOCK_M: tl.constexpr, BLOCK_N: tl.constexpr):
    pid = tl.program_id(0)
    rows = pid * BLOCK_M + tl.arange(0, BLOCK_M)
    cols = tl.arange(0, BLOCK_N)
    mask = (rows[:, None] < M) & (cols[None, :] < N)
    x = tl.load(x_ptr + rows[:, None] * stride + cols[None, :], mask=mask, other=-float('inf'))
    row_max = tl.max(x, axis=1)
    shifted = tl.where(mask, x - row_max[:, None], 0.0)
    row_mean = tl.sum(shifted, axis=1) / N
    tl.store(max_ptr + rows, row_max, mask=rows < M)
    tl.store(mean_ptr + rows, row_mean, mask=rows < M)


@triton.jit
def argmax_kernel(x_ptr, index_ptr, value_ptr, n_cols, stride, BLOCK: tl.constexpr):
    row = tl.program_id(0)
    cols = tl.arange(0, BLOCK)
    x = tl.load(x_ptr + row * stride + cols, mask=cols < n_cols, other=-float('inf'))
    value, index = tl.max(x, axis=0, return_indices=True)
    tl.store(index_ptr + row, index)
    tl.store(value_ptr + row, value)


@triton.jit
def cumsum_kernel(x_ptr, out_ptr, n, BLOCK: tl.constexpr):
    pid = tl.program_id(0)
    offs = pid * BLOCK + tl.arange(0, BLOCK)
    x = tl.load(x_ptr + offs, mask=offs < n, other=0)
    tl.store(out_ptr + offs, tl.cumsum(x, axis=0), mask=offs < n)


@triton.jit
def clip_rows_kernel(x_ptr, out_ptr, n_cols, stride, limit, BLOCK: tl.constexpr):
    row = tl.program_id(0)
    cols = tl.arange(0, BLOCK)
    mask = cols < n_cols
    x = tl.load(x_ptr + row * stride + cols, mask=mask, other=0.0)
    norm = tl.sqrt(tl.sum(x * x, axis=0))
    if norm > limit:
        x = x * (limit / norm)
    tl.store(out_ptr + row * stride + cols, x, mask=mask)


@triton.jit
def histogram_kernel(x_ptr, bins_ptr, n, n_bins, BLOCK: tl.constexpr):
    pid = tl.program_id(0)
    offs = pid * BLOCK + tl.arange(0, BLOCK)
    mask = offs < n
    x = tl.load(x_ptr + offs, mask=mask, other=0)
    bucket = tl.minimum(tl.maximum(x, 0), n_bins - 1)
    tl.atomic_add(bins_ptr + bucket, 1, mask=mask, sem='relaxed')


@triton.jit
def attention_fwd_kernel(Q, K, V, Out, sm_scale, N_CTX, stride_h, stride_m,
                         BLOCK_M: tl.constexpr, BLOCK_N: tl.constexpr, HEAD_DIM: tl.constexpr):
    """softmax(Q K^T * sm_scale) V for one head and a block of queries (the online softmax of the
    Triton fused-attention tutorial, without causal masking): fp16 inputs, fp32 accumulation."""
    start_m = tl.program_id(0)
    head = tl.program_id(1)
    offs_m = start_m * BLOCK_M + tl.arange(0, BLOCK_M)
    offs_n = tl.arange(0, BLOCK_N)
    offs_d = tl.arange(0, HEAD_DIM)
    base = head * stride_h
    q = tl.load(Q + base + offs_m[:, None] * stride_m + offs_d[None, :], mask=offs_m[:, None] < N_CTX, other=0.0)
    m_i = tl.zeros([BLOCK_M], dtype=tl.float32) - float('inf')
    l_i = tl.zeros([BLOCK_M], dtype=tl.float32) + 1.0
    acc = tl.zeros([BLOCK_M, HEAD_DIM], dtype=tl.float32)
    qk_scale = sm_scale * 1.44269504
    for start_n in range(0, N_CTX, BLOCK_N):
        cols = start_n + offs_n
        k = tl.load(K + base + cols[None, :] * stride_m + offs_d[:, None], mask=cols[None, :] < N_CTX, other=0.0)
        qk = tl.dot(q, k, input_precision="ieee")
        qk = tl.where(cols[None, :] < N_CTX, qk * qk_scale, -float('inf'))
        m_ij = tl.maximum(m_i, tl.max(qk, 1))
        p = tl.math.exp2(qk - m_ij[:, None])
        l_ij = tl.sum(p, 1)
        alpha = tl.math.exp2(m_i - m_ij)
        l_i = l_i * alpha + l_ij
        acc = acc * alpha[:, None]
        v = tl.load(V + base + cols[:, None] * stride_m + offs_d[None, :], mask=cols[:, None] < N_CTX, other=0.0)
        acc = tl.dot(p.to(tl.float16), v, acc, input_precision="ieee")
        m_i = m_ij
    acc = acc / l_i[:, None]
    tl.store(Out + base + offs_m[:, None] * stride_m + offs_d[None, :], acc.to(tl.float16), mask=offs_m[:, None] < N_CTX)


@triton.jit
def block_ptr_matmul_kernel(a_ptr, b_ptr, c_ptr, M, N, K, stride_am, stride_ak, stride_bk, stride_bn, stride_cm, stride_cn,
                            BLOCK_M: tl.constexpr, BLOCK_N: tl.constexpr, BLOCK_K: tl.constexpr):
    """C = A @ B with block pointers (tl.make_block_ptr, tl.advance, boundary checks)."""
    pid_m = tl.program_id(0)
    pid_n = tl.program_id(1)
    a_block = tl.make_block_ptr(a_ptr, shape=(M, K), strides=(stride_am, stride_ak), offsets=(pid_m * BLOCK_M, 0),
                                block_shape=(BLOCK_M, BLOCK_K), order=(1, 0))
    b_block = tl.make_block_ptr(b_ptr, shape=(K, N), strides=(stride_bk, stride_bn), offsets=(0, pid_n * BLOCK_N),
                                block_shape=(BLOCK_K, BLOCK_N), order=(1, 0))
    acc = tl.zeros((BLOCK_M, BLOCK_N), dtype=tl.float32)
    for k in range(0, K, BLOCK_K):
        a = tl.load(a_block, boundary_check=(0, 1), padding_option="zero")
        b = tl.load(b_block, boundary_check=(0, 1), padding_option="zero")
        acc = tl.dot(a, b, acc, input_precision="ieee")
        a_block = tl.advance(a_block, (0, BLOCK_K))
        b_block = tl.advance(b_block, (BLOCK_K, 0))
    c_block = tl.make_block_ptr(c_ptr, shape=(M, N), strides=(stride_cm, stride_cn), offsets=(pid_m * BLOCK_M, pid_n * BLOCK_N),
                                block_shape=(BLOCK_M, BLOCK_N), order=(1, 0))
    tl.store(c_block, acc, boundary_check=(0, 1))


@triton.jit
def transpose_kernel(x_ptr, y_ptr, M, N, BLOCK: tl.constexpr):
    """y = x^T through a register transpose (tl.trans)."""
    pid_m = tl.program_id(0)
    pid_n = tl.program_id(1)
    rm = pid_m * BLOCK + tl.arange(0, BLOCK)
    rn = pid_n * BLOCK + tl.arange(0, BLOCK)
    tile = tl.load(x_ptr + rm[:, None] * N + rn[None, :], mask=(rm[:, None] < M) & (rn[None, :] < N), other=0.0)
    tl.store(y_ptr + rn[:, None] * M + rm[None, :], tl.trans(tile), mask=(rn[:, None] < N) & (rm[None, :] < M))


@triton.jit
def collatz_kernel(x_ptr, steps_ptr, n, BLOCK: tl.constexpr):
    """Per program: steps of the Collatz map for one starting value (a data-dependent while loop on a scalar)."""
    pid = tl.program_id(0)
    if pid < n:
        v = tl.load(x_ptr + pid)
        steps = 0
        while v != 1:
            v = tl.where(v % 2 == 0, v // 2, 3 * v + 1)
            steps += 1
        tl.store(steps_ptr + pid, steps)


@triton.jit
def precise_ops_kernel(x_ptr, y_ptr, out_ptr, fma_ptr, bits_ptr, n, BLOCK: tl.constexpr):
    """Exact operations: NaN-propagating maximum and minimum, clamp, correctly rounded division and
    square root, fused multiply-add, and the high half of a 32-bit product."""
    offs = tl.program_id(0) * BLOCK + tl.arange(0, BLOCK)
    mask = offs < n
    x = tl.load(x_ptr + offs, mask=mask, other=1.0)
    y = tl.load(y_ptr + offs, mask=mask, other=1.0)
    hi = tl.maximum(x, y, propagate_nan=tl.PropagateNan.ALL)
    lo = tl.minimum(x, y, propagate_nan=tl.PropagateNan.ALL)
    c = tl.clamp(x, -1.5, 2.0)
    q = tl.math.div_rn(x, y)
    r = tl.math.sqrt_rn(tl.abs(y))
    f = tl.math.fma(x, y, c)
    out = hi + lo * 2.0 + c * 3.0 + q * 5.0 + r * 7.0
    tl.store(out_ptr + offs, out, mask=mask)
    tl.store(fma_ptr + offs, f, mask=mask)
    u = x.to(tl.uint32, bitcast=True)
    v = y.to(tl.uint32, bitcast=True)
    tl.store(bits_ptr + offs, tl.umulhi(u, v), mask=mask)


@triton.jit
def gelu_libdevice_kernel(x_ptr, erf_ptr, tanh_ptr, n, BLOCK: tl.constexpr):
    """GELU both ways, through libdevice (as Liger-Kernel and others write them): 0.5 x (1 + erf(x / sqrt 2))
    and the tanh approximation 0.5 x (1 + tanh(sqrt(2 / pi) (x + 0.044715 x^3)))."""
    offs = tl.program_id(0) * BLOCK + tl.arange(0, BLOCK)
    mask = offs < n
    x = tl.load(x_ptr + offs, mask=mask, other=0.0)
    exact = 0.5 * x * (1.0 + libdevice.erf(x * 0.7071067811865476))
    inner = 0.7978845608028654 * (x + 0.044715 * x * x * x)
    approx = 0.5 * x * (1.0 + libdevice.tanh(inner))
    tl.store(erf_ptr + offs, exact, mask=mask)
    tl.store(tanh_ptr + offs, approx, mask=mask)


@triton.jit
def newton_sqrt_kernel(x_ptr, out_ptr, iterations_ptr, n_elements, BLOCK_SIZE: tl.constexpr):
    """A while loop carrying a tensor: Newton steps for sqrt until the block's largest relative error
    is small (at most 50 steps)."""
    pid = tl.program_id(0)
    offsets = pid * BLOCK_SIZE + tl.arange(0, BLOCK_SIZE)
    mask = offsets < n_elements
    a = tl.load(x_ptr + offsets, mask=mask, other=1.0)
    y = tl.where(a > 1.0, a, 1.0)
    err = tl.max(tl.abs(y * y - a) / a, axis=0)
    steps = 0
    while (err > 1e-6) & (steps < 50):
        y = 0.5 * (y + a / y)
        err = tl.max(tl.abs(y * y - a) / a, axis=0)
        steps += 1
    tl.store(out_ptr + offsets, y, mask=mask)
    tl.store(iterations_ptr + pid, steps)


@triton.jit
def complex_mul_kernel(a_ptr, b_ptr, out_ptr, n_elements, BLOCK_SIZE: tl.constexpr):
    """Interleaved complex numbers (re, im pairs): tl.split of a loaded [BLOCK, 2] tile, tl.join of the
    product's parts."""
    pid = tl.program_id(0)
    offsets = pid * BLOCK_SIZE + tl.arange(0, BLOCK_SIZE)
    pair = 2 * offsets[:, None] + tl.arange(0, 2)[None, :]
    mask = (offsets < n_elements)[:, None] & (tl.arange(0, 2)[None, :] < 2)
    ar, ai = tl.split(tl.load(a_ptr + pair, mask=mask, other=0.0))
    br, bi = tl.split(tl.load(b_ptr + pair, mask=mask, other=0.0))
    product = tl.join(ar * br - ai * bi, ar * bi + ai * br)
    tl.store(out_ptr + pair, product, mask=mask)


# Attention backward (non-causal, float32 with ieee dots), in the structure of the Triton tutorial's
# fused attention: the forward pass's row logsumexp (natural log) is an input; delta = rowsum(o * do);
# one program per block of keys accumulates dK and dV over every query block, one program per block of
# queries accumulates dQ over every key block.
@triton.jit
def attention_bwd_preprocess_kernel(o_ptr, do_ptr, delta_ptr, N_CTX, HEAD_DIM: tl.constexpr, BLOCK_M: tl.constexpr):
    offs_m = tl.program_id(0) * BLOCK_M + tl.arange(0, BLOCK_M)
    offs_d = tl.arange(0, HEAD_DIM)
    mask = offs_m[:, None] < N_CTX
    o = tl.load(o_ptr + offs_m[:, None] * HEAD_DIM + offs_d[None, :], mask=mask, other=0.0)
    do = tl.load(do_ptr + offs_m[:, None] * HEAD_DIM + offs_d[None, :], mask=mask, other=0.0)
    tl.store(delta_ptr + offs_m, tl.sum(o * do, axis=1), mask=offs_m < N_CTX)


@triton.jit
def attention_bwd_dkdv_kernel(q_ptr, k_ptr, v_ptr, do_ptr, lse_ptr, delta_ptr, dk_ptr, dv_ptr, sm_scale, N_CTX,
                              HEAD_DIM: tl.constexpr, BLOCK_M: tl.constexpr, BLOCK_N: tl.constexpr):
    offs_n = tl.program_id(0) * BLOCK_N + tl.arange(0, BLOCK_N)
    offs_d = tl.arange(0, HEAD_DIM)
    k = tl.load(k_ptr + offs_n[:, None] * HEAD_DIM + offs_d[None, :])
    v = tl.load(v_ptr + offs_n[:, None] * HEAD_DIM + offs_d[None, :])
    dk = tl.zeros([BLOCK_N, HEAD_DIM], dtype=tl.float32)
    dv = tl.zeros([BLOCK_N, HEAD_DIM], dtype=tl.float32)
    for start_m in range(0, N_CTX, BLOCK_M):
        offs_m = start_m + tl.arange(0, BLOCK_M)
        qT = tl.load(q_ptr + offs_m[None, :] * HEAD_DIM + offs_d[:, None])
        lse = tl.load(lse_ptr + offs_m)
        pT = tl.exp(tl.dot(k, qT, input_precision="ieee") * sm_scale - lse[None, :])
        do = tl.load(do_ptr + offs_m[:, None] * HEAD_DIM + offs_d[None, :])
        dv += tl.dot(pT, do, input_precision="ieee")
        dpT = tl.dot(v, tl.trans(do), input_precision="ieee")
        delta = tl.load(delta_ptr + offs_m)
        dsT = pT * (dpT - delta[None, :])
        dk += tl.dot(dsT, tl.trans(qT), input_precision="ieee")
    tl.store(dk_ptr + offs_n[:, None] * HEAD_DIM + offs_d[None, :], dk * sm_scale)
    tl.store(dv_ptr + offs_n[:, None] * HEAD_DIM + offs_d[None, :], dv)


@triton.jit
def attention_bwd_dq_kernel(q_ptr, k_ptr, v_ptr, do_ptr, lse_ptr, delta_ptr, dq_ptr, sm_scale, N_CTX,
                            HEAD_DIM: tl.constexpr, BLOCK_M: tl.constexpr, BLOCK_N: tl.constexpr):
    offs_m = tl.program_id(0) * BLOCK_M + tl.arange(0, BLOCK_M)
    offs_d = tl.arange(0, HEAD_DIM)
    q = tl.load(q_ptr + offs_m[:, None] * HEAD_DIM + offs_d[None, :])
    do = tl.load(do_ptr + offs_m[:, None] * HEAD_DIM + offs_d[None, :])
    lse = tl.load(lse_ptr + offs_m)
    delta = tl.load(delta_ptr + offs_m)
    dq = tl.zeros([BLOCK_M, HEAD_DIM], dtype=tl.float32)
    for start_n in range(0, N_CTX, BLOCK_N):
        offs_n = start_n + tl.arange(0, BLOCK_N)
        kT = tl.load(k_ptr + offs_n[None, :] * HEAD_DIM + offs_d[:, None])
        vT = tl.load(v_ptr + offs_n[None, :] * HEAD_DIM + offs_d[:, None])
        p = tl.exp(tl.dot(q, kT, input_precision="ieee") * sm_scale - lse[:, None])
        dp = tl.dot(do, vT, input_precision="ieee")
        ds = p * (dp - delta[:, None])
        dq += tl.dot(ds, tl.trans(kT), input_precision="ieee")
    tl.store(dq_ptr + offs_m[:, None] * HEAD_DIM + offs_d[None, :], dq * sm_scale)
