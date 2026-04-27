#!/usr/bin/env python3
#
# Copyright Red Hat
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#

import logging

import gitlab
import requests
from attrs import define, field

from committime import CommitMetric
from pelorus.timeutil import parse_tz_aware
from pelorus.utils import set_up_requests_session

from .collector_base import AbstractCommitCollector, check_provider_support

_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"


@define(kw_only=True)
class GitLabCommitCollector(AbstractCommitCollector):
    session: requests.Session = field(factory=requests.Session, init=False)

    # Cache GitLab clients per server to avoid reconnecting per commit
    _gitlab_clients: dict = field(factory=dict, init=False)

    # Cache GitLab project objects per namespaced path to avoid N+1 lookups
    _project_cache: dict = field(factory=dict, init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        set_up_requests_session(
            self.session, self.tls_verify, username=self.username, token=self.token
        )

    def _connect_to_gitlab(self, metric) -> gitlab.Gitlab:
        """Method to connect to Gitlab instance."""
        git_server = metric.git_server

        if git_server in self._gitlab_clients:
            return self._gitlab_clients[git_server]

        gitlab_client = None

        if self.token:
            logging.debug("Connecting to GitLab server (authenticated): %s", git_server)
            gitlab_client = gitlab.Gitlab(
                git_server,
                private_token=self.token,
                api_version=4,
                session=self.session,
            )
        else:
            logging.debug(
                "Connecting to GitLab server (unauthenticated): %s", git_server
            )
            gitlab_client = gitlab.Gitlab(
                git_server, api_version=4, session=self.session
            )

        self._gitlab_clients[git_server] = gitlab_client
        return gitlab_client

    def get_commit_time(self, metric: CommitMetric):
        """Fetch commit timestamp from GitLab API for the given metric."""

        git_server = metric.git_server

        check_provider_support(git_server, "gitlab")

        gl = self._connect_to_gitlab(metric)
        if not gl:
            return metric

        project_namespace = metric.repo_group
        project_name = metric.repo_project

        # namespaced project allows to get it by it's name
        project_namespaced = "%s/%s" % (project_namespace, project_name)

        cache_key = (git_server, project_namespaced)
        project = self._project_cache.get(cache_key)

        if project is None:
            try:
                logging.debug("Getting project: %s", project_namespaced)
                project = gl.projects.get(project_namespaced)
                self._project_cache[cache_key] = project
            except Exception:
                logging.error(
                    "Failed to get project: %s, repo: %s for build %s",
                    metric.repo_url, project_name, metric.build_name,
                    exc_info=True,
                )
                raise
        try:
            # get the commit from the project using the hash
            short_hash = metric.commit_hash[:8]
            commit = project.commits.get(short_hash)

            commit_time_str: str = commit.committed_date
            metric.commit_time = commit_time_str
            metric.commit_timestamp = parse_tz_aware(
                commit_time_str, format=_DATETIME_FORMAT
            ).timestamp()
            metric.commit_link = commit.web_url
        except Exception:
            logging.error(
                "Failed processing commit time for build %s",
                metric.build_name,
                exc_info=True,
            )
            raise
        return metric
