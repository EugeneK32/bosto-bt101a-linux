"""Minimal ArtInChip USB protocol for BOSTO BT-101A (33c3:0e02).

Based on USB/authentication code from hevnsnt/artinchip-linux/tinyscreen.py
Copyright (c) 2026 Bill Swearingen (hevnsnt), MIT License.
See LICENSE-artinchip-linux.txt.
"""
import os
import secrets
import struct

import usb.core
import usb.util
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

FRAME_START_MAGIC = 0xA1C62B01
AUTH_DEV_MAGIC = 0xA1C62B10
AUTH_HOST_MAGIC = 0xA1C62B11
VID, PID = 0x33C3, 0x0E02
EP_OUT, EP_IN = 0x01, 0x81
MAX_TRANSFER = 4096 * 64

# RSA public key embedded in the original ArtInChip Linux protocol implementation.
RSA_PUB_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAybdtvB1uNA4XICh+xJi1
KJWO0GYal4lNiW69zSMIJFGzb2wkiFBX2txFaH5ZYh0TYdwmjzBqinzTsWhIasW3
rl9QN5cv73zFalO3J4hADXz1g7hlHVB0BKDD280NUKUGAbwDv+KMHTprs+B/T4QU
a0s4RBNnN4fMPk2H0UAWU1jKAvMYjh/YR+MLYbl04ZCLlOfX9zQjRBVan7aLARQg
v5QRahAlAoBsYK864VrBKq91lRCXt4XP5d/sDtZM7kGcpLi2i4xHtRct37M+bkZv
Lf/3aVpAVsqZy5P2NXEe6HMv4Q+YP6QKz2wuk3xWYHWFn+88ydjv394tN28rjl56
hwIDAQAB
-----END PUBLIC KEY-----"""


def find_device():
    return usb.core.find(idVendor=VID, idProduct=PID)


def setup_device(dev):
    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
    except usb.core.USBError:
        pass
    usb.util.claim_interface(dev, 0)


def get_params(dev):
    data = dev.ctrl_transfer(
        usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
        0, 0, 0, 256, timeout=5000)
    ver, chip, fmt, bus, modes, w, h, fps = struct.unpack_from('<8H', data, 0)
    return w, h, fmt, fps


def bulk_out(dev, data, timeout=5000):
    dev.write(EP_OUT, data, timeout=timeout)


def bulk_in(dev, size=256, timeout=5000):
    return bytes(dev.read(EP_IN, size, timeout=timeout))


def authenticate(dev):
    """ArtInChip's two-phase RSA challenge-response handshake."""
    pk = serialization.load_pem_public_key(RSA_PUB_PEM)
    challenge = os.urandom(secrets.randbelow(244) + 1)
    encrypted = pk.encrypt(challenge, asym_padding.PKCS1v15())
    bulk_out(dev, struct.pack('<IIHHII', AUTH_DEV_MAGIC, 0x100, 0, 0, 0, AUTH_DEV_MAGIC))
    bulk_out(dev, encrypted)
    response = bulk_in(dev, 256, timeout=3000)
    if len(response) < len(challenge) or response[:len(challenge)] != challenge:
        return False
    bulk_out(dev, struct.pack('<IIHHII', AUTH_HOST_MAGIC, 0x100, 0, 0, 0, AUTH_HOST_MAGIC))
    signed = bulk_in(dev, 256, timeout=3000)
    n, e = pk.public_numbers().n, pk.public_numbers().e
    m = pow(int.from_bytes(signed, 'big'), e, n)
    m_bytes = m.to_bytes((n.bit_length() + 7) // 8, 'big')
    if m_bytes[:2] != b'\x00\x01':
        raise ValueError('Bad PKCS#1 padding')
    idx = 2
    while idx < len(m_bytes) and m_bytes[idx] == 0xFF:
        idx += 1
    if idx >= len(m_bytes) or m_bytes[idx] != 0:
        raise ValueError('Bad PKCS#1 separator')
    bulk_out(dev, m_bytes[idx + 1:])
    return True


def send_frame(dev, jpeg_data, media_format, frame_id):
    bulk_out(dev, struct.pack('<IIHHII', FRAME_START_MAGIC, len(jpeg_data),
                              frame_id & 0xFFFF, media_format, 0, FRAME_START_MAGIC))
    for pos in range(0, len(jpeg_data), MAX_TRANSFER):
        bulk_out(dev, bytes(jpeg_data[pos:pos + MAX_TRANSFER]), timeout=10000)
