"""Ping Minecraft Java (Server List Ping) en Python pur, sans dépendance."""
import json
import socket


class MinecraftPingError(Exception):
    pass


def _varint(n: int) -> bytes:
    n &= 0xFFFFFFFF
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def _read_varint_sock(sock: socket.socket) -> int:
    result = 0
    for i in range(5):
        b = sock.recv(1)
        if not b:
            raise MinecraftPingError("Connexion fermée par le serveur")
        result |= (b[0] & 0x7F) << (7 * i)
        if not b[0] & 0x80:
            return result
    raise MinecraftPingError("VarInt invalide")


def _read_varint_buf(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    for i in range(5):
        if pos >= len(buf):
            raise MinecraftPingError("Réponse tronquée")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << (7 * i)
        if not b & 0x80:
            return result, pos
    raise MinecraftPingError("VarInt invalide")


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(min(65536, n - len(data)))
        if not chunk:
            raise MinecraftPingError("Réponse tronquée")
        data += chunk
    return data


def ping(host: str, port: int, timeout: float = 5.0) -> dict:
    """Retourne le JSON de statut du serveur (players.online, version, ...)."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            host_b = host.encode("utf-8")
            handshake = _varint(0) + _varint(-1) + _varint(len(host_b)) + host_b \
                + port.to_bytes(2, "big") + _varint(1)
            s.sendall(_varint(len(handshake)) + handshake)
            s.sendall(b"\x01\x00")  # Status Request

            length = _read_varint_sock(s)
            if length <= 0 or length > 4 * 1024 * 1024:
                raise MinecraftPingError(f"Taille de réponse invalide ({length})")
            data = _recv_exact(s, length)
    except OSError as e:
        raise MinecraftPingError(f"{host}:{port} injoignable : {e}") from e

    packet_id, pos = _read_varint_buf(data, 0)
    if packet_id != 0:
        raise MinecraftPingError(f"Paquet inattendu (id {packet_id})")
    str_len, pos = _read_varint_buf(data, pos)
    try:
        return json.loads(data[pos:pos + str_len].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise MinecraftPingError(f"Réponse JSON invalide : {e}") from e
