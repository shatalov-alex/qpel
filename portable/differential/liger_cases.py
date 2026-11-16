"""Seeded cases for the Liger-Kernel corpus (corpus/liger, frontends/triton/corpora.py): rows of
Liger's shapes (one program per row, n_cols below, at and above a block, padded strides), with
the constexprs of corpus/liger/kernels.json (n_cols is a constexpr of some kernels: 200)."""
import numpy as np

from .simulate import Case

SHAPES = ((1, 1, 0), (3, 17, 0), (4, 256, 0), (5, 200, 3), (2, 129, 1))  # rows, cols, stride padding
FIXED = ((1, 200, 0), (3, 200, 0), (4, 200, 5))  # kernels whose n_cols is the constexpr 200


def f32(rng, n, scale=1.0, shift=0.0):
    return (rng.standard_normal(n) * scale + shift).astype(np.float32)


def zeros(n):
    return np.zeros(n, np.float32)


def rows_case(shapes, buffers, values, label):
    sizes = {f'{name}_size': (len(array), 1, 1) for name, array in buffers.items()}
    return Case(dict(shapes, **sizes), buffers, values, label)


def rms_norm_forward(rng):
    out = []
    for rows, cols, pad in SHAPES:
        s = cols + pad
        out.append(rows_case({'programs': (rows, 1, 1)},
                             {'Y_ptr': zeros(rows * s), 'X_ptr': f32(rng, rows * s, 2), 'W_ptr': f32(rng, cols),
                              'RSTD_ptr': zeros(rows)},
                             {'Y_row_stride': s, 'X_row_stride': s, 'W_row_stride': 0, 'RSTD_row_stride': 1,
                              'n_cols': cols, 'eps': 1e-6, 'offset': 1.0 if rows % 2 else 0.0}, f'{rows}x{cols} stride {s}'))
    return out


def fused_add_rms_norm_forward(rng):
    out = []
    for rows, cols, pad in SHAPES:
        s = cols + pad
        out.append(rows_case({'programs': (rows, 1, 1)},
                             {'Y_ptr': zeros(rows * s), 'S_ptr': zeros(rows * s), 'X_ptr': f32(rng, rows * s, 2),
                              'R_ptr': f32(rng, rows * s), 'W_ptr': f32(rng, cols), 'RSTD_ptr': zeros(rows)},
                             {'Y_row_stride': s, 'S_row_stride': s, 'X_row_stride': s, 'R_row_stride': s,
                              'W_row_stride': 0, 'RSTD_row_stride': 1, 'n_cols': cols, 'eps': 1e-6, 'offset': 0.0},
                             f'{rows}x{cols} stride {s}'))
    return out


def layer_norm_forward(rng):
    out = []
    for rows, cols, pad in SHAPES:
        s = cols + pad
        out.append(rows_case({'programs': (rows, 1, 1)},
                             {'Y_ptr': zeros(rows * s), 'X_ptr': f32(rng, rows * s, 2, 0.5), 'W_ptr': f32(rng, cols),
                              'B_ptr': f32(rng, cols), 'Mean_ptr': zeros(rows), 'RSTD_ptr': zeros(rows)},
                             {'Y_row_stride': s, 'X_row_stride': s, 'W_row_stride': 0, 'B_row_stride': 0,
                              'Mean_row_stride': 1, 'RSTD_row_stride': 1, 'n_cols': cols, 'eps': 1e-5},
                             f'{rows}x{cols} stride {s}'))
    return out


def layer_norm_backward(rng):
    """Four rows per program (rows_per_program), the last program's rows ending early; Mean and
    RSTD as the forward pass gives them (float64 here, rounded)."""
    out = []
    for rows, cols, pad in ((1, 1, 0), (4, 64, 0), (6, 200, 2), (9, 129, 0)):
        s = cols + pad
        x = f32(rng, rows * s, 2, 0.5)
        xs = x.reshape(rows, s)[:, :cols].astype(np.float64)
        mean = xs.mean(axis=1)
        rstd = 1 / np.sqrt(xs.var(axis=1) + 1e-5)
        programs = -(-rows // 4)
        out.append(rows_case({'programs': (programs, 1, 1)},
                             {'X_ptr': x, 'W_ptr': f32(rng, cols), 'Mean_ptr': mean.astype(np.float32),
                              'RSTD_ptr': rstd.astype(np.float32), 'DX_ptr': zeros(rows * s),
                              'DW_ptr': zeros(programs * cols), 'DB_ptr': zeros(programs * cols),
                              'DY_ptr': f32(rng, rows * s)},
                             {'stride_x': s, 'stride_mean': 1, 'stride_rstd': 1, 'stride_dx': s, 'stride_dw': cols,
                              'stride_db': cols, 'stride_dy': s, 'n_rows': rows, 'n_cols': cols},
                             f'{rows}x{cols} stride {s}'))
    return out


def poly_norm_forward(rng):
    out = []
    for rows, cols, pad in SHAPES:
        s = cols + pad
        out.append(rows_case({'programs': (rows, 1, 1)},
                             {'Y_ptr': zeros(rows * s), 'X_ptr': f32(rng, rows * s), 'W_ptr': f32(rng, 3),
                              'B_ptr': f32(rng, 1), 'RSTD_ptr': zeros(rows * 3)},
                             {'Y_row_stride': s, 'X_row_stride': s, 'RSTD_row_stride': 3, 'n_cols': cols, 'eps': 1e-6},
                             f'{rows}x{cols} stride {s}'))
    return out


def gated(names, multiplier):
    """swiglu and geglu: rows of the constexpr width 200, in place for the backward kernels."""
    def cases(rng):
        out = []
        for rows, cols, pad in FIXED:
            s = cols + pad
            buffers = {name: f32(rng, rows * s, 2) for name in names}
            values = {'stride': s} | ({'gate_multiplier': m} if (m := multiplier(rows)) is not None else {})
            out.append(rows_case({'programs': (rows, 1, 1)}, buffers, values, f'{rows}x{cols} stride {s}'))
        return out
    return cases


def softmax_forward(rng):
    out = []
    for rows, cols, pad in SHAPES:
        s = cols + pad
        x = f32(rng, rows * s, 3)
        out.append(rows_case({'programs': (rows, 1, 1)}, {'Y_ptr': zeros(rows * s), 'X_ptr': x},
                             {'Y_row_stride': s, 'X_row_stride': s, 'n_cols': cols}, f'{rows}x{cols} stride {s}'))
    return out


def softmax_backward(rng):
    out = []
    for rows, cols, pad in SHAPES:
        s = cols + pad
        y = np.abs(f32(rng, rows * s)) / max(cols, 1)
        out.append(rows_case({'programs': (rows, 1, 1)},
                             {'dy_ptr': f32(rng, rows * s), 'y_ptr': y.astype(np.float32), 'dx_ptr': zeros(rows * s)},
                             {'dy_stride': s, 'y_stride': s, 'dx_stride': s, 'n_cols': cols}, f'{rows}x{cols} stride {s}'))
    return out


def relu_squared_forward(rng):
    out = []
    for rows, cols, pad in FIXED:
        s = cols + pad
        out.append(rows_case({'programs': (rows, 1, 1)}, {'Y_ptr': zeros(rows * s), 'X_ptr': f32(rng, rows * s, 2)},
                             {'Y_stride': s, 'X_stride': s}, f'{rows}x{cols} stride {s}'))
    return out


def relu_squared_backward(rng):
    out = []
    for rows, cols, pad in FIXED:
        s = cols + pad
        out.append(rows_case({'programs': (rows, 1, 1)},
                             {'dX_ptr': zeros(rows * s), 'dY_ptr': f32(rng, rows * s), 'X_ptr': f32(rng, rows * s, 2)},
                             {'dX_stride': s, 'dY_stride': s, 'X_stride': s}, f'{rows}x{cols} stride {s}'))
    return out


def kldiv_forward(rng):
    """reduction sum (1): one loss per row, the row folded in blocks of 256 (a loop over blocks)."""
    out = []
    for rows, cols, pad in ((1, 1, 0), (3, 100, 0), (2, 256, 4), (2, 600, 0)):
        s = cols + pad
        y = np.log(np.abs(f32(rng, rows * s)) + 0.01).astype(np.float32)
        gt = np.abs(f32(rng, rows * s)) / cols
        gt[::7] = 0.0  # eps keeps log(0) out
        out.append(rows_case({'programs': (rows, 1, 1)}, {'y_ptr': y, 'gt_ptr': gt.astype(np.float32), 'loss_ptr': zeros(rows)},
                             {'y_stride': s, 'gt_stride': s, 'loss_stride': 1, 'n_cols': cols, 'eps': 1e-10},
                             f'{rows}x{cols} stride {s}'))
    return out


def rowwise_element_mul(rng):
    out = []
    for rows, cols, pad in ((1, 1, 0), (3, 64, 0), (2, 65, 3), (4, 300, 0)):
        s = cols + pad
        out.append(rows_case({'programs': (rows, 1, 1)}, {'X_ptr': f32(rng, rows * s), 'grad_output_ptr': f32(rng, rows)},
                             {'X_stride': s, 'grad_output_stride': 1, 'n_cols': cols}, f'{rows}x{cols} stride {s}'))
    return out


def geglu_backward(rng):
    """In place (da into a, db into b). da holds 1 - tanh(u)^2, which cancels where tanh is near +-1:
    an error of tanh (a bounded function, MATH_ULPS of 1) is scaled by the terms around it, so da
    compares within epsilons of |dc * b| * (1 + |a| * sqrt(2/pi) * (1 + 3 * 0.044715 * a^2)) (twice
    that: tanh^2 doubles the error), and db = dc * 0.5 * a * (1 + tanh) of |dc * a|."""
    out = gated(('dc', 'a', 'b'), lambda rows: None)(rng)
    for case in out:
        dc, a, b = (np.asarray(case.inputs[k], np.float64) for k in ('dc', 'a', 'b'))
        k = np.sqrt(2 / np.pi) * (1 + 3 * 0.044715 * a * a)
        case.scales = {'a': 2 * np.abs(dc * b) * (1 + np.abs(a) * k), 'b': np.abs(dc * a)}
    return out


CASES = {
    'liger_rms_norm_forward': rms_norm_forward,
    'liger_fused_add_rms_norm_forward': fused_add_rms_norm_forward,
    'liger_layer_norm_forward': layer_norm_forward,
    'liger_layer_norm_backward': layer_norm_backward,
    'liger_poly_norm_forward': poly_norm_forward,
    'liger_swiglu_forward': gated(('a_ptr', 'b_ptr', 'c_ptr'), lambda rows: 1.0 if rows == 1 else 0.5),
    'liger_swiglu_backward': gated(('dc_ptr', 'a_ptr', 'b_ptr'), lambda rows: 1.0 if rows == 1 else 0.5),
    'liger_geglu_tanh_forward': gated(('a', 'b', 'c'), lambda rows: None),
    'liger_geglu_tanh_backward': geglu_backward,
    'liger_softmax_single_block_forward': softmax_forward,
    'liger_softmax_single_block_backward': softmax_backward,
    'liger_relu_squared_forward': relu_squared_forward,
    'liger_relu_squared_backward': relu_squared_backward,
    'liger_kldiv_forward': kldiv_forward,
    'liger_rowwise_element_mul': rowwise_element_mul,
}


# -- the second batch: backward passes, 2D blocks of rows, modulation, RoPE, group norm --

def rms_norm_backward(rng):
    """rows_per_program rows per program (the last program's end early); RSTD as the forward pass
    gives it; dW one row of partial sums per program."""
    out = []
    for rows, cols, pad, per in ((1, 1, 0, 1), (4, 64, 0, 2), (6, 200, 3, 4), (9, 129, 0, 4)):
        s = cols + pad
        x = f32(rng, rows * s, 2)
        xs = x.reshape(rows, s)[:, :cols].astype(np.float64)
        rstd = (1 / np.sqrt((xs * xs).mean(axis=1) + 1e-6)).astype(np.float32)
        programs = -(-rows // per)
        out.append(rows_case({'programs': (programs, 1, 1)},
                             {'dY_ptr': f32(rng, rows * s), 'dX_ptr': zeros(rows * s), 'X_ptr': x, 'W_ptr': f32(rng, cols),
                              'RSTD_ptr': rstd, 'dW_ptr': zeros(programs * cols)},
                             {'dY_row_stride': s, 'dX_row_stride': s, 'X_row_stride': s, 'W_row_stride': 0,
                              'RSTD_row_stride': 1, 'dW_row_stride': cols, 'n_rows': rows, 'n_cols': cols,
                              'offset': 0.0, 'rows_per_program': per}, f'{rows}x{cols} stride {s}, {per} rows each'))
    return out


def fused_add_rms_norm_backward(rng):
    """rows_per_program is 4 (a constexpr); dS_out, the residual's own gradient, is added."""
    out = []
    for rows, cols, pad in ((1, 1, 0), (4, 64, 0), (6, 200, 3), (9, 129, 0)):
        s = cols + pad
        x = f32(rng, rows * s, 2)
        xs = x.reshape(rows, s)[:, :cols].astype(np.float64)
        rstd = (1 / np.sqrt((xs * xs).mean(axis=1) + 1e-6)).astype(np.float32)
        programs = -(-rows // 4)
        out.append(rows_case({'programs': (programs, 1, 1)},
                             {'dY_ptr': f32(rng, rows * s), 'dS_out_ptr': f32(rng, rows * s), 'dX_ptr': zeros(rows * s),
                              'X_ptr': x, 'W_ptr': f32(rng, cols), 'RSTD_ptr': rstd, 'dW_ptr': zeros(programs * cols)},
                             {'dY_row_stride': s, 'dS_out_row_stride': s, 'dX_row_stride': s, 'X_row_stride': s,
                              'W_row_stride': 0, 'RSTD_row_stride': 1, 'dW_row_stride': cols, 'n_rows': rows,
                              'n_cols': cols, 'offset': 0.0}, f'{rows}x{cols} stride {s}'))
    return out


def poly_norm_backward(rng):
    out = []
    for rows, cols, pad in ((1, 1, 0), (4, 64, 0), (6, 200, 3), (9, 129, 0)):
        s = cols + pad
        x = f32(rng, rows * s)
        xs = x.reshape(rows, s)[:, :cols].astype(np.float64)
        rstd = np.stack([1 / np.sqrt((xs ** (2 * p)).mean(axis=1) + 1e-6) for p in (3, 2, 1)], axis=1)
        programs = -(-rows // 4)
        out.append(rows_case({'programs': (programs, 1, 1)},
                             {'dY_ptr': f32(rng, rows * s), 'dX_ptr': zeros(rows * s), 'X_ptr': x, 'W_ptr': f32(rng, 3),
                              'RSTD_ptr': rstd.astype(np.float32).reshape(-1), 'dW_ptr': zeros(programs * 3),
                              'dB_ptr': zeros(programs)},
                             {'dY_row_stride': s, 'dX_row_stride': s, 'X_row_stride': s, 'RSTD_row_stride': 3,
                              'dW_row_stride': 3, 'n_rows': rows, 'n_cols': cols}, f'{rows}x{cols} stride {s}'))
        # dW_p = sum over a program's rows of rstd_p * sum(dY * x^p), dB = sum(dY): sums of signed terms,
        # compared within epsilons of the same sums of magnitudes
        dy = np.abs(np.asarray(out[-1].inputs['dY_ptr'], np.float64).reshape(rows, s)[:, :cols])
        terms = np.stack([rstd[:, j] * (dy * np.abs(xs) ** p).sum(axis=1) for j, p in enumerate((3, 2, 1))], axis=1)
        program = np.arange(rows) // 4
        out[-1].scales = {'dW_ptr': np.stack([terms[program == g].sum(axis=0) for g in range(programs)]).reshape(-1),
                          'dB_ptr': np.array([dy[program == g].sum() for g in range(programs)])}
    return out


def kldiv_backward(rng):
    """-target per element (log_target off), a row per program in blocks of 256."""
    out = []
    for rows, cols, pad in ((1, 1, 0), (3, 100, 0), (2, 256, 4), (2, 600, 0)):
        s = cols + pad
        target = np.abs(f32(rng, rows * s)) / cols
        target[::5] = 0.0
        out.append(rows_case({'programs': (rows, 1, 1)}, {'target_ptr': target.astype(np.float32), 'new_grads_ptr': zeros(rows * s)},
                             {'target_stride': s, 'new_grads_stride': s, 'n_cols': cols}, f'{rows}x{cols} stride {s}'))
    return out


def block_rms_norm_forward(rng):
    """BLOCK_ROW = 4 rows per program, as a 2D tile (rows past n_rows masked)."""
    out = []
    for rows, cols, pad in ((1, 1, 0), (4, 64, 0), (6, 100, 3), (9, 128, 0)):
        s = cols + pad
        out.append(rows_case({'programs': (-(-rows // 4), 1, 1)},
                             {'Y_ptr': zeros(rows * s), 'X_ptr': f32(rng, rows * s, 2), 'W_ptr': f32(rng, cols),
                              'RSTD_ptr': zeros(rows)},
                             {'Y_row_stride': s, 'X_row_stride': s, 'W_row_stride': 0, 'RSTD_row_stride': 1,
                              'n_rows': rows, 'n_cols': cols, 'eps': 1e-6, 'offset': 1.0}, f'{rows}x{cols} stride {s}'))
    return out


def modulated_rms_norm_forward(rng):
    """Scale and shift rows shared by rows_per_modulation = 2 rows."""
    out = []
    for rows, cols, pad in SHAPES:
        s = cols + pad
        mods = -(-rows // 2)
        out.append(rows_case({'programs': (rows, 1, 1)},
                             {'Y_ptr': zeros(rows * s), 'X_ptr': f32(rng, rows * s, 2), 'W_ptr': f32(rng, cols),
                              'Scale_ptr': f32(rng, mods * cols, 0.5), 'Shift_ptr': f32(rng, mods * cols),
                              'RSTD_ptr': zeros(rows)},
                             {'Y_row_stride': s, 'X_row_stride': s, 'W_row_stride': 0, 'Scale_row_stride': cols,
                              'Shift_row_stride': cols, 'RSTD_row_stride': 1, 'n_cols': cols, 'eps': 1e-6, 'offset': 0.0},
                             f'{rows}x{cols} stride {s}'))
    return out


def swiglu_fused_gate_up_forward(rng):
    """Each input row holds the gate (ffn_size = 200) then the up projection."""
    out = []
    for rows, pad in ((1, 0), (3, 0), (4, 5)):
        s_in, s_out = 400 + pad, 200 + pad
        out.append(rows_case({'programs': (rows, 1, 1)}, {'y_ptr': f32(rng, rows * s_in, 2), 'c_ptr': zeros(rows * s_out)},
                             {'in_stride': s_in, 'out_stride': s_out}, f'{rows} rows, strides {s_in}, {s_out}'))
    return out


def dyt_forward(rng):
    """N = 200 columns in blocks of 256 (grid x), one row per grid y."""
    out = []
    for rows in (1, 3, 5):
        out.append(rows_case({'programs': (1, rows, 1)},
                             {'X': f32(rng, rows * 200, 2), 'Y': zeros(rows * 200), 'Alpha': np.array([0.7], np.float32),
                              'Gamma': f32(rng, 200), 'Beta': f32(rng, 200)}, {}, f'{rows}x200'))
    return out


def rope(rng):
    """In place on q (4 heads) and k (2 heads) of head dimension 16, rotary over all 16; cos and sin
    rows of 8 per position (cos_bs = 1), sequences of sl positions in batches of 2."""
    out = []
    for sl in (1, 3, 8):
        rows = 2 * sl
        cos_sin = rng.uniform(-np.pi, np.pi, sl * 8)
        out.append(rows_case({'programs': (rows, 1, 1)},
                             {'q_ptr': f32(rng, rows * 64), 'k_ptr': f32(rng, rows * 32),
                              'cos': np.cos(cos_sin).astype(np.float32), 'sin': np.sin(cos_sin).astype(np.float32)},
                             {'q_row_stride': 64, 'k_row_stride': 32, 'cos_row_stride': 8, 'sin_row_stride': 8, 'sl': sl},
                             f'batch 2 x {sl} positions'))
    return out


def group_norm_forward(rng):
    """X of shape (batch, groups, hidden) with channels_per_group channels in each group's hidden
    elements; the variance is E[x^2] - E[x]^2, so inputs are centred (its cancellation stays small)."""
    out = []
    for batch, groups, cpg, per_channel in ((1, 1, 1, 1), (2, 3, 2, 50), (3, 2, 4, 80)):
        hidden = cpg * per_channel
        n = batch * groups * hidden
        out.append(rows_case({'programs': (batch, groups, 1)},
                             {'Y_ptr': zeros(n), 'X_ptr': f32(rng, n, 2), 'Mean_ptr': zeros(batch * groups),
                              'RSTD_ptr': zeros(batch * groups), 'W_ptr': f32(rng, groups * cpg), 'B_ptr': f32(rng, groups * cpg)},
                             {'Y_row_stride': groups * hidden, 'Y_col_stride': hidden, 'X_row_stride': groups * hidden,
                              'X_col_stride': hidden, 'Mean_row_stride': groups, 'Mean_col_stride': 1,
                              'RSTD_row_stride': groups, 'RSTD_col_stride': 1, 'hidden_size': hidden,
                              'channels_per_group': cpg, 'eps': 1e-5}, f'{batch}x{groups}x{hidden}'))
    return out


def tvd(rng):
    """Total variation distance with labels (HAS_LABEL), reduction sum (1): a row whose label is the
    ignore index (-100) returns early with zero gradients; grads in place, one loss per row."""
    out = []
    for rows, cols in ((1, 1), (3, 100), (4, 600)):
        p = np.abs(f32(rng, rows * cols)) + 0.01
        q = np.abs(f32(rng, rows * cols)) + 0.01
        labels = np.arange(rows, dtype=np.int64)
        labels[1::2] = -100
        out.append(rows_case({'programs': (rows, 1, 1)},
                             {'p_ptr': (p / cols).astype(np.float32), 'q_ptr': (q / cols).astype(np.float32),
                              'loss_ptr': zeros(rows), 'grads_ptr': np.full(rows * cols, 9.0, np.float32), 'label_ptr': labels},
                             {'p_stride': cols, 'q_stride': cols, 'loss_stride': 1, 'grads_stride': cols, 'n_cols': cols,
                              'scale': 0.5}, f'{rows}x{cols}'))
    return out


def cross_entropy(rng):
    """Liger's fused cross entropy (hard labels, mean reduction, gradients in place of the logits):
    rows whose label is the ignore index (-100) return early with zero gradients; vocabularies below,
    at and above the 256 block; the online softmax runs over blocks of 256."""
    out = []
    for rows, vocab in ((1, 1), (3, 100), (4, 256), (3, 700)):
        x = f32(rng, rows * vocab, 3)
        labels = rng.integers(0, vocab, rows).astype(np.int64)
        if rows > 1:
            labels[1] = -100
        kept = float((labels != -100).sum())
        out.append(rows_case({'programs': (rows, 1, 1)},
                             {'X_ptr': x, 'Y_ptr': labels, 'weight_ptr': zeros(1), 'loss_ptr': zeros(rows),
                              'z_loss_ptr': zeros(1), 'token_accuracy_ptr': zeros(1),
                              'predicted_tokens_ptr': np.zeros(1, np.int64)},
                             {'X_stride': vocab, 'Y_stride': 1, 'loss_stride': 1, 'token_accuracy_stride': 1,
                              'predicted_tokens_stride': 1, 'n_cols': vocab, 'n_non_ignore': kept,
                              'sum_non_ignore_weight': kept, 'weight_sum': 0.0, 'ignore_index': -100, 'softcap': 0.0},
                             f'{rows}x{vocab}'))
    return out


CASES.update({
    'liger_rms_norm_backward': rms_norm_backward,
    'liger_fused_add_rms_norm_backward': fused_add_rms_norm_backward,
    'liger_poly_norm_backward': poly_norm_backward,
    'liger_kldiv_backward': kldiv_backward,
    'liger_block_rms_norm_forward': block_rms_norm_forward,
    'liger_modulated_rms_norm_forward': modulated_rms_norm_forward,
    'liger_swiglu_fused_gate_up_forward': swiglu_fused_gate_up_forward,
    'liger_dyt_forward': dyt_forward,
    'liger_rope': rope,
    'liger_group_norm_forward': group_norm_forward,
    'liger_tvd': tvd,
    'liger_cross_entropy': cross_entropy,
})
