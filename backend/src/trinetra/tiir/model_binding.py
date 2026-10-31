"""IMP-0293: observed model content, contract and adapter identity; no trust verdict."""
from trinetra.domain.assets import AssetId
from trinetra.domain.base import DomainModel
from trinetra.domain.digests import DigestSha256
from trinetra.domain.execution_assurance import ExecutionAssuranceClass
from trinetra.tiir.binding_text import BindingText


class ModelBinding(DomainModel):
    model_asset_id: AssetId
    model_content_digest: DigestSha256
    model_io_contract_digest: DigestSha256
    adapter_id: BindingText
    adapter_version: BindingText
    execution_assurance_class: ExecutionAssuranceClass

    def to_cbor_value(self) -> dict[int, object]:
        return {1: self.model_asset_id.to_wire(), 2: self.model_content_digest.raw,
                3: self.model_io_contract_digest.raw, 4: self.adapter_id, 5: self.adapter_version,
                6: self.execution_assurance_class.to_wire()}
