"""Triton kernels for qpel's Triton frontend (frontends/triton/README.md).

Written for qpel in the style of the Triton tutorials (vector addition, fused softmax, layer
norm; https://triton-lang.org/main/getting-started/tutorials/, MIT): one program per block or
row, masked loads and stores, reductions over a block, loops over a row in blocks.
"""
import triton
import triton.language as tl


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
