#!/usr/bin/env python3
"""Rewrite the local AGWPE callsign while preserving Pat's mailbox callsign.

Pat uses one callsign for both its Winlink session and AGWPE registration.
This small, local proxy lets the webmail session use the logged-in mailbox
call while Dire Wolf sees the configured packet-station call.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import struct
from dataclasses import dataclass


HEADER = struct.Struct("<B3s c B c B 10s 10s I I")
CALL_SIZE = 10


def call_bytes(value: str) -> bytes:
    encoded = value.strip().upper().encode("ascii")
    if not encoded or len(encoded) > CALL_SIZE:
        raise ValueError(f"invalid callsign: {value!r}")
    return encoded.ljust(CALL_SIZE, b"\0")


def clean_call(value: bytes) -> str:
    return value.split(b"\0", 1)[0].decode("ascii", "ignore").strip().upper()


@dataclass
class Frame:
    raw_header: list[object]
    data: bytes

    @property
    def kind(self) -> bytes:
        return self.raw_header[2]

    @property
    def source(self) -> bytes:
        return self.raw_header[6]

    @property
    def destination(self) -> bytes:
        return self.raw_header[7]

    def encode(self) -> bytes:
        header = self.raw_header.copy()
        header[8] = len(self.data)
        return HEADER.pack(*header) + self.data


async def read_frame(reader: asyncio.StreamReader) -> Frame:
    header_bytes = await reader.readexactly(HEADER.size)
    values = list(HEADER.unpack(header_bytes))
    length = values[8]
    if length > 1024 * 1024:
        raise ValueError("AGWPE frame is too large")
    return Frame(values, await reader.readexactly(length))


def rewrite_outbound(frame: Frame, mailbox: bytes, packet: bytes) -> Frame:
    # Registration, connection control, connected data, and UI frames carry
    # the caller identity in the AGWPE From field.
    if frame.kind in {b"X", b"x", b"C", b"v", b"d", b"D", b"Y", b"M"}:
        if frame.source == mailbox or frame.kind in {b"X", b"x"}:
            frame.raw_header[6] = packet
    return frame


def rewrite_inbound(frame: Frame, mailbox: bytes, packet: bytes) -> Frame:
    # Dire Wolf addresses responses/data to the packet station. Pat must see
    # those frames addressed to the mailbox callsign it registered.
    if frame.destination == packet:
        frame.raw_header[7] = mailbox
    return frame


async def pipe_frames(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    outbound: bool,
    state: dict[str, bytes],
    packet: bytes,
) -> None:
    while True:
        frame = await read_frame(reader)
        if outbound:
            if frame.kind == b"X":
                state["mailbox"] = frame.source
            mailbox = state.get("mailbox", frame.source)
            frame = rewrite_outbound(frame, mailbox, packet)
        else:
            mailbox = state.get("mailbox", packet)
            frame = rewrite_inbound(frame, mailbox, packet)
        writer.write(frame.encode())
        await writer.drain()


async def handle_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_host: str,
    upstream_port: int,
    packet_call: bytes,
) -> None:
    upstream_reader = upstream_writer = None
    state: dict[str, bytes] = {}
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(upstream_host, upstream_port)
        await asyncio.gather(
            pipe_frames(client_reader, upstream_writer, True, state, packet_call),
            pipe_frames(upstream_reader, client_writer, False, state, packet_call),
        )
    except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError):
        pass
    finally:
        for writer in (client_writer, upstream_writer):
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=8002)
    parser.add_argument("--upstream-host", default="127.0.0.1")
    parser.add_argument("--upstream-port", type=int, default=8000)
    parser.add_argument("--packet-call", default=os.environ.get("N0JCG_PACKET_CALLSIGN", "N0JCG-3"))
    args = parser.parse_args()
    packet_call = call_bytes(args.packet_call)
    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, args.upstream_host, args.upstream_port, packet_call),
        args.listen_host,
        args.listen_port,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
