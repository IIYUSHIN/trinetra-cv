"""IMP-0296: Supervisor-observed runtime identities, not hardware attestation."""
from trinetra.domain.base import DomainModel
from trinetra.domain.digests import DigestSha256
from trinetra.tiir.binding_text import BindingText


class RuntimeBinding(DomainModel):
    runner_image_digest: DigestSha256
    resource_profile_digest: DigestSha256
    runtime_provider: BindingText
    requested_device: BindingText

    def to_cbor_value(self) -> dict[int, object]:
        return {1: self.runner_image_digest.raw, 2: self.resource_profile_digest.raw,
                3: self.runtime_provider, 4: self.requested_device}
