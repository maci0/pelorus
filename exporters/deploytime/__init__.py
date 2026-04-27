import types
from datetime import datetime

from attrs import field, frozen

from provider_common.openshift import convert_datetime


@frozen
class DeployTimeMetric:
    name: str
    namespace: str
    labels: types.MappingProxyType = field(converter=types.MappingProxyType)
    deploy_time: datetime = field(converter=convert_datetime)
    image_sha: str
    _hash: int = field(init=False, repr=False, eq=False, hash=False)

    def __attrs_post_init__(self):
        h = hash(
            (
                self.name,
                self.namespace,
                hash(tuple(self.labels.items())),
                self.deploy_time,
                self.image_sha,
            )
        )
        object.__setattr__(self, "_hash", h)

    @property
    def deploy_time_timestamp(self) -> float:
        return self.deploy_time.timestamp()

    def __hash__(self):
        return self._hash


__all__ = ["DeployTimeMetric"]
