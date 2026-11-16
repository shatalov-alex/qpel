"""The Triton corpora (frontends/triton/README.md): each a folder with kernels.json and the
committed build/ (TTIR, TTGIR, PTX and launch metadata) of every kernel it lists.

- corpus/triton: kernels written for qpel in the style of Triton's tutorials (kernels.py).
- corpus/liger: Liger-Kernel's own ops modules, vendored unmodified (BSD-2, LICENSE, SHA256SUMS);
  kernels.json names each `function` as module:name, and the folder is the package root.

Kernel names are unique across corpora (Liger's start with liger_); a kernel's fixture is
tests/fixtures/qpel_ir/triton_<name>.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
CORPORA = {'triton': HERE/'corpus/triton', 'liger': HERE/'corpus/liger'}


def specs(corpus):
    return json.loads((CORPORA[corpus]/'kernels.json').read_text())


def kernels(corpus=None):
    """Kernel names, of one corpus or of all."""
    return [name for c in ([corpus] if corpus else CORPORA) for name in specs(c)]


def corpus_of(name):
    for corpus in CORPORA:
        if name in specs(corpus):
            return corpus
    raise KeyError(f'{name} is in no Triton corpus')


def folder(name):
    return CORPORA[corpus_of(name)]


def build(name):
    return folder(name)/'build'


def source(name):
    """What compile.py and reference.py import, relative to the corpus folder: kernels.py, or the
    folder itself for a vendored package (its kernels.json names functions as module:name)."""
    return 'kernels.py' if (folder(name)/'kernels.py').exists() else '.'


def metadata(name):
    return json.loads((build(name)/f'{name}.json').read_text())


def ttir(name):
    return (build(name)/f'{name}.ttir').read_text()
