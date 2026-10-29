"""Assemble the build tree from the resolved modules.

out/<device>/tree/ has the layout of this repository (drivers/ kits/ patches/ scripts/ package/)
plus refer/, build/ and .cache/ for the scripts that expect them. Modules are symbolic links to
their sources, so edits to a local module take effect on the next build; scripts are copied, since
they locate the tree from their own real path. The device package appears as kits/<kit>.
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path

from .util import Error, log


def _is_link(path):
    """True for a symbolic link or a Windows junction."""
    if path.is_symlink():
        return True
    if os.name == 'nt':
        try:
            attributes = os.lstat(path).st_file_attributes
        except OSError:
            return False
        return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return False


def _unlink(path):
    if path.is_symlink() or not path.is_dir():
        path.unlink()
        return
    try:
        os.rmdir(path)          # junctions are removed with rmdir
    except OSError:
        shutil.rmtree(path)


def _clear_links(directory):
    if directory.is_dir():
        for entry in directory.iterdir():
            if _is_link(entry):
                _unlink(entry)


def _link(target, link):
    link.parent.mkdir(parents=True, exist_ok=True)
    if _is_link(link) or link.exists():
        _unlink(link)
    target = Path(target).resolve()
    try:
        link.symlink_to(target)
        return
    except OSError:
        if os.name != 'nt':
            raise
    # Windows without Developer Mode: a directory junction needs no privilege.
    if target.is_dir():
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise Error(f'cannot link {link} -> {target}: {result.stderr.strip()}')
    else:
        shutil.copy2(target, link)


def _copy_scripts(source, destination):
    for path in sorted(Path(source).rglob('*')):
        relative = path.relative_to(source)
        if path.is_dir() or '__pycache__' in path.parts or relative.parts[0] == 'unixnu':
            continue
        target = destination / relative
        if target.exists() and target.stat().st_size == path.stat().st_size and target.stat().st_mtime >= path.stat().st_mtime:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def assemble(ctx, modules):
    log(f'tree {ctx.tree}')
    tree = ctx.tree
    tree.mkdir(parents=True, exist_ok=True)
    for folder in ('drivers', 'kits', 'patches', 'refer'):
        _clear_links(tree / folder)
        (tree / folder).mkdir(exist_ok=True)
    for name in ('build', '.cache'):
        (tree / name).mkdir(exist_ok=True)
    # Proprietary files (device repository: scripts/extract-files.py) stay in the device repository and are shared by every tree.
    firmware = ctx.device / '.cache/firmware'
    firmware.mkdir(parents=True, exist_ok=True)
    link = tree / '.cache/firmware'
    if _is_link(link):
        _unlink(link)
    if not link.exists():
        _link(firmware, link)
    kit = ctx.board.get('board', {}).get('kit', ctx.name)
    for (kind, name), module in sorted(modules.items()):
        if module.path is None:
            if kind == 'refer':
                continue   # fetched when a step needs it
            raise Error(f'{kind} {name} has no source yet; run "unixnu fetch"')
        if kind == 'package':
            if name == 'main':
                _link(module.path, tree / 'package')
            else:
                _link(module.path, tree / 'kits' / kit)
        else:
            _link(module.path, tree / {'driver': 'drivers', 'kit': 'kits', 'patch': 'patches', 'refer': 'refer'}[kind] / name)
    if not _is_link(tree / 'package'):
        raise Error('no package module: this repository or --local-package must provide package/')
    (tree / 'scripts').mkdir(exist_ok=True)
    for base in (ctx.root, ctx.device):
        if (base / 'scripts').is_dir():
            _copy_scripts(base / 'scripts', tree / 'scripts')
    return tree
