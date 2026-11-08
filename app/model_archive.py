"""Read a ZIP whose paths and output sizes are fixed by a trusted model catalog."""
import os
import stat
import struct
import zipfile
import zlib
from pathlib import PurePosixPath

from .model_pack import ModelPackError

# Bound the central directory before ZipFile reads it into memory. The packaged
# Python runtime uses the same ZIP/ZIP64 footer parser as ZipFile.
MAX_CENTRAL_DIRECTORY = 1024 * 1024
BLOCK_SIZE = 1024 * 1024


def open_zip(stream):
    footer = zipfile._EndRecData(stream)
    if not footer:
        raise ModelPackError('invalid_archive')
    if footer[zipfile._ECD_SIZE] > MAX_CENTRAL_DIRECTORY:
        raise ModelPackError('archive_limits')
    if footer[zipfile._ECD_DISK_NUMBER] or footer[zipfile._ECD_DISK_START]:
        raise ModelPackError('invalid_archive')
    return zipfile.ZipFile(stream)


def archive_members(archive, pack):
    files = {str(file.path): file for file in pack.files}
    directories = {str(parent) for file in pack.files for parent in file.path.parents
                   if parent != PurePosixPath('.')}
    seen = set()
    selected = {}
    for member in archive.infolist():
        name = member.orig_filename
        is_dir = member.is_dir()
        key = name.removesuffix('/') if is_dir else name
        parts = key.split('/')
        kind = stat.S_IFMT(member.external_attr >> 16)
        if (name != member.filename or '\\' in name or ':' in name
                or any(part in ('', '.', '..') for part in parts)
                or kind not in (0, stat.S_IFDIR if is_dir else stat.S_IFREG)):
            raise ModelPackError('unsafe_archive_entry', name)
        folded = key.lower()
        if folded in seen:
            raise ModelPackError('duplicate_path', key)
        seen.add(folded)
        if (member.flag_bits & 1
                or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
            raise ModelPackError('unsupported_archive_entry', key)
        if is_dir:
            if key not in directories or member.file_size:
                raise ModelPackError('unexpected_entry', key)
            continue
        if key not in files:
            raise ModelPackError('unexpected_entry', key)
        maximum_compressed = member.file_size + member.file_size // 100 + 65536
        if (member.file_size != files[key].size
                or member.compress_size > maximum_compressed
                or (member.compress_type == zipfile.ZIP_STORED
                    and member.compress_size != member.file_size)):
            raise ModelPackError('archive_size_mismatch', key)
        selected[key] = member
    missing = files.keys() - selected.keys()
    if missing:
        raise ModelPackError('missing', min(missing))
    return selected


def archive_chunks(archive, member, check_cancelled):
    # ZipFile.open validates the local header name and rejects overlapping
    # entries. Read the bounded compressed region ourselves because ZipExtFile
    # clips expanded data to the declared size before returning it.
    with archive.open(member):
        pass
    stream = archive.fp
    stream.seek(member.header_offset + 26)
    name_size, extra_size = struct.unpack('<HH', stream.read(4))
    stream.seek(name_size + extra_size, os.SEEK_CUR)
    remaining = member.compress_size
    budget = member.file_size
    crc = 0
    inflater = (zlib.decompressobj(-15)
                if member.compress_type == zipfile.ZIP_DEFLATED else None)
    while remaining:
        check_cancelled()
        compressed = stream.read(min(remaining, BLOCK_SIZE))
        if not compressed:
            raise ModelPackError('invalid_archive', member.filename)
        remaining -= len(compressed)
        while compressed:
            check_cancelled()
            if inflater is None:
                chunk, compressed = compressed, b''
            else:
                chunk = inflater.decompress(compressed, min(BLOCK_SIZE, budget + 1))
                compressed = inflater.unconsumed_tail
                if inflater.unused_data or (inflater.eof and remaining):
                    raise ModelPackError('invalid_archive', member.filename)
            budget -= len(chunk)
            if budget < 0:
                raise ModelPackError('archive_size_mismatch', member.filename)
            if chunk:
                crc = zlib.crc32(chunk, crc)
                yield chunk
    if budget or (inflater is not None and not inflater.eof) or crc != member.CRC:
        raise ModelPackError('invalid_archive', member.filename)
