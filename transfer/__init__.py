"""
transfer package: File transfer and integrity verification.
"""

from transfer.integrity import calculate_sha256, get_file_metadata, verify_file
from transfer.receiver import FileReceiver
from transfer.sender import FileSender, ClientFileTransferManager
from transfer.server_handler import FileTransferServerHandler, register_file_transfer_handlers

__all__ = [
    "calculate_sha256",
    "get_file_metadata",
    "verify_file",
    "FileReceiver",
    "FileSender",
    "ClientFileTransferManager",
    "FileTransferServerHandler",
    "register_file_transfer_handlers",
]
