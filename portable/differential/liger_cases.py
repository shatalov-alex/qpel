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
