"""Apply an explicitly requested model pack at a safe process boundary."""
import json
from pathlib import Path

from .job_checkpoints import atomic_json
from .model_pack import ModelPackError, verify_directory


class ModelActivation:
    def __init__(self, data: Path, candidate: Path, pack):
        self.receipt = data / 'model-activation.json'
        self.candidate = candidate
        self.pack = pack
        self.boot_error = None

    def read(self):
        try:
            record = json.loads(self.receipt.read_text())
            if not isinstance(record, dict):
                raise ValueError
            return record
        except FileNotFoundError:
            return {}
        except (ValueError, OSError):
            return {'error': 'invalid_receipt'}

    def request(self):
        record = self.read()
        atomic_json(self.receipt, record | {'pending': self.candidate.name, 'error': None})

    def boot(self, busy: bool):
        record = self.read()
        if not record or record.get('error') == 'invalid_receipt':
            return None
        active = record.get('active') == self.candidate.name
        pending = record.get('pending')
        if busy:
            return self.candidate / 'models' if active else None
        if not pending and not active:
            return None
        try:
            if pending and pending != self.candidate.name:
                raise ModelPackError('incompatible_version')
            verify_directory(self.candidate, self.pack)
        except (ModelPackError, OSError):
            try:
                atomic_json(self.receipt, {'error': 'verification_failed'})
            except OSError:
                self.boot_error = 'receipt_write_failed'
            return None
        if pending:
            try:
                atomic_json(self.receipt, {'active': pending})
            except OSError:
                self.boot_error = 'receipt_write_failed'
                return self.candidate / 'models' if active else None
        return self.candidate / 'models'

    def status(self, root):
        record = self.read()
        return {'active_version': self.pack.version if root == self.candidate / 'models' else None,
                'activation_pending': bool(record.get('pending')),
                'activation_error': self.boot_error or record.get('error')}
