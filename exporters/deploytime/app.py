import logging
import time
from typing import Iterable

from attrs import field, frozen
from kubernetes.dynamic import DynamicClient
from prometheus_client import Counter, Gauge, start_http_server
from prometheus_client.core import REGISTRY, GaugeMetricFamily

import pelorus
from deploytime import DeployTimeMetric
from pelorus.config import load_and_log, no_env_vars
from pelorus.config.converters import comma_separated
from pelorus.timeutil import METRIC_TIMESTAMP_THRESHOLD_MINUTES, is_out_of_date_timestamp
from provider_common import format_app_name
from provider_common.openshift import (
    filter_pods_by_replica_uid,
    get_and_log_namespaces,
    get_images_from_pod,
    get_owner_object_from_child,
    get_running_pods,
)


_collection_duration = Gauge(
    "pelorus_deploytime_collection_duration_seconds",
    "Duration of the last deploytime metric collection in seconds",
)
_collection_errors = Counter(
    "pelorus_deploytime_collection_errors_total",
    "Total number of deploytime metric collection errors",
)


@frozen
class DeployTimeCollector(pelorus.AbstractPelorusExporter):
    client: DynamicClient = field(metadata=no_env_vars())
    namespaces: set[str] = field(factory=set, converter=comma_separated(set))
    prod_label: str = field(default=pelorus.DEFAULT_PROD_LABEL)

    def __attrs_post_init__(self):
        if self.namespaces and (self.prod_label != pelorus.DEFAULT_PROD_LABEL):
            logging.warning("If NAMESPACES are given, PROD_LABEL is ignored.")

    def describe(self) -> list[GaugeMetricFamily]:
        return [
            GaugeMetricFamily(
                "deploy_timestamp",
                "Deployment timestamp",
                labels=["namespace", "app", "image_sha"],
            )
        ]

    def collect(self) -> Iterable[GaugeMetricFamily]:
        logging.debug("collect: start")
        start = time.monotonic()
        try:
            metrics = self.generate_metrics()

            deploy_timestamp_metric = GaugeMetricFamily(
                "deploy_timestamp",
                "Deployment timestamp",
                labels=["namespace", "app", "image_sha"],
            )

            number_of_dropped = 0

            for m in metrics:
                if not is_out_of_date_timestamp(m.deploy_time_timestamp):
                    logging.debug(
                        "Collected deploy_timestamp{namespace=%s, app=%s, image=%s} %s (%s)",
                        m.namespace,
                        m.name,
                        m.image_sha,
                        m.deploy_time_timestamp,
                        m.deploy_time,
                    )
                    deploy_timestamp_metric.add_metric(
                        [m.namespace, format_app_name(m.name), m.image_sha],
                        m.deploy_time_timestamp,
                        timestamp=m.deploy_time_timestamp,
                    )
                else:
                    number_of_dropped += 1
                    logging.debug(
                        "Deployment too old to be collected: deploy_timestamp{namespace=%s, app=%s, image=%s} %s (%s)",
                        m.namespace,
                        m.name,
                        m.image_sha,
                        m.deploy_time_timestamp,
                        m.deploy_time,
                    )
            if number_of_dropped:
                logging.debug(
                    "Number of deployments that are older than %smin and won't be collected: %s",
                    METRIC_TIMESTAMP_THRESHOLD_MINUTES,
                    number_of_dropped,
                )
            yield deploy_timestamp_metric
        except Exception:
            _collection_errors.inc()
            logging.error("Deploy time metric collection failed", exc_info=True)
            yield GaugeMetricFamily(
                "deploy_timestamp",
                "Deployment timestamp",
                labels=["namespace", "app", "image_sha"],
            )
        finally:
            duration = time.monotonic() - start
            _collection_duration.set(duration)
            logging.debug("collect: finished in %.2fs", duration)

    def generate_metrics(self) -> Iterable[DeployTimeMetric]:
        namespaces = get_and_log_namespaces(
            self.client, self.namespaces, self.prod_label
        )

        if not namespaces:
            return []

        logging.debug("generate_metrics: start")

        pods = get_running_pods(self.client, namespaces, self.app_label)

        # Build dictionary with controllers and retrieved pods
        replica_pods_dict = filter_pods_by_replica_uid(pods)

        for uid, pod in replica_pods_dict.items():
            replicas = get_owner_object_from_child(self.client, uid, pod)

            replica = replicas.get(uid)
            if replica is None:
                logging.debug(
                    "Parent object not found for pod %s (uid=%s), skipping",
                    pod.metadata.name, uid,
                )
                continue

            # Since there could be multiple containers (images) per pod,
            # we push one metric per image/container in the pod template
            images = get_images_from_pod(pod)

            for sha in images.keys():
                metric = DeployTimeMetric(
                    name=pod.metadata.labels[self.app_label],
                    namespace=pod.metadata.namespace,
                    labels=pod.metadata.labels,
                    deploy_time=replica.metadata.creationTimestamp,
                    image_sha=sha,
                )
                yield metric


def set_up(prod: bool = True) -> DeployTimeCollector:
    pelorus.setup_logging(prod=prod)
    dyn_client = pelorus.utils.get_k8s_client()

    collector = load_and_log(DeployTimeCollector, other=dict(client=dyn_client))

    REGISTRY.register(collector)
    return collector


if __name__ == "__main__":
    set_up()
    start_http_server(8080)
    while True:
        time.sleep(1)
