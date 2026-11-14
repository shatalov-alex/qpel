"""Metal source text as it is compiled: quoted includes pasted in, function constants given
their values, unused template instantiations left out. Shared by the Apple GPU runner
(metal.py) and the CPU one (metal_cpu.py); needs no PyTorch."""
import re


def inline_includes(path, include=(), done=None):
    """Source text with its quoted #includes from the library's folders pasted in, once each
    (compile_shader takes one string; ggml embeds its headers the same way)."""
    import re
    from pathlib import Path
    done = set() if done is None else done
    path = Path(path)
    out = []
    for line in path.read_text().splitlines():
        found = re.match(r'\s*#\s*include\s+"([^"]+)"', line)
        if found:
            for folder in [path.parent] + [Path(f) for f in include]:
                candidate = (folder/found.group(1)).resolve()
                if candidate.exists():
                    if candidate not in done:
                        done.add(candidate)
                        out.append(inline_includes(candidate, include, done))
                    break
            else:
                out.append(line)
            continue
        out.append(line)
    return '\n'.join(out)


def specialise(text, constants):
    """Give function constants their values, as pipeline creation would: compile_shader cannot
    pass constant values, and Metal will not build a pipeline for an unspecialised kernel."""
    import re
    for name, value in (constants or {}).items():
        literal = ('true' if value else 'false') if isinstance(value, bool) else repr(value)
        text, count = re.subn(r'constant\s+(\w+)\s+' + re.escape(name) + r'\s*\[\[\s*function_constant\([^]]*\)\s*\]\]\s*;',
                              lambda m: f'constant {m.group(1)} {name} = {literal};', text)
        if count != 1:
            raise ValueError(f'function constant {name}: found {count} declarations')
    return text


INSTANTIATION = re.compile(r'template\s*\[\[\s*host_name\("([^"]+)"\)\s*\]\][^;{]*;')


def instantiating_only(text, names):
    """The source without the explicit template instantiations (`template [[host_name("x")]]
    kernel ... f<T>;`) of kernels other than `names`. Nothing else changes, so the kernels that
    run are the original's; ggml-metal.metal has 544 such instantiations, and compiling them
    all for every specialisation is most of the cost of running one of its kernels."""
    return INSTANTIATION.sub(lambda m: m.group(0) if m.group(1) in names else '', text)
