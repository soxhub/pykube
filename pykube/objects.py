import copy
import json
import os.path as op

import six

from six.moves.urllib.parse import urlencode
from .exceptions import ObjectDoesNotExist
from .mixins import ReplicatedMixin, ScalableMixin
from .query import ObjectManager
from .utils import obj_merge


DEFAULT_NAMESPACE = "default"


@six.python_2_unicode_compatible
class APIObject(object):

    objects = ObjectManager()
    base = None
    namespace = None

    def __init__(self, api, obj):
        self.api = api
        self.set_obj(obj)

    def set_obj(self, obj):
        self.obj = obj
        self._original_obj = copy.deepcopy(obj)

    def __repr__(self):
        return "<{kind} {name}>".format(kind=self.kind, name=self.name)

    def __str__(self):
        return self.name

    @property
    def name(self):
        return self.obj["metadata"]["name"]

    @property
    def annotations(self):
        return self.obj["metadata"].get("annotations", {})

    def api_kwargs(self, **kwargs):
        kw = {}
        # Construct url for api request
        obj_list = kwargs.pop("obj_list", False)
        if obj_list:
            kw["url"] = self.endpoint
        else:
            operation = kwargs.pop("operation", "")
            kw["url"] = op.normpath(op.join(self.endpoint, self.name, operation))
        params = kwargs.pop("params", None)
        if params is not None:
            query_string = urlencode(params)
            kw["url"] = "{}{}".format(kw["url"], "?{}".format(query_string) if query_string else "")
        if self.base:
            kw["base"] = self.base
        kw["version"] = self.version
        if self.namespace is not None:
            kw["namespace"] = self.namespace
        kw.update(kwargs)
        return kw

    def exists(self, ensure=False):
        r = self.api.get(**self.api_kwargs())
        if r.status_code not in {200, 404}:
            self.api.raise_for_status(r)
        if not r.ok:
            if ensure:
                raise ObjectDoesNotExist("{} does not exist.".format(self.name))
            else:
                return False
        return True

    def create(self):
        r = self.api.post(**self.api_kwargs(data=json.dumps(self.obj), obj_list=True))
        self.api.raise_for_status(r)
        self.set_obj(r.json())

    def reload(self):
        r = self.api.get(**self.api_kwargs())
        self.api.raise_for_status(r)
        self.set_obj(r.json())

    def watch(self):
        return self.__class__.objects(
            self.api,
            namespace=self.namespace
        ).filter(field_selector={
            "metadata.name": self.name
        }).watch()

    def update(self):
        self.obj = obj_merge(self.obj, self._original_obj)
        r = self.api.patch(**self.api_kwargs(
            headers={"Content-Type": "application/merge-patch+json"},
            data=json.dumps(self.obj),
        ))
        self.api.raise_for_status(r)
        self.set_obj(r.json())

    def delete(self):
        r = self.api.delete(**self.api_kwargs())
        if r.status_code != 404:
            self.api.raise_for_status(r)


class NamespacedAPIObject(APIObject):

    objects = ObjectManager(namespace=DEFAULT_NAMESPACE)

    @property
    def namespace(self):
        if self.obj["metadata"].get("namespace"):
            return self.obj["metadata"]["namespace"]
        else:
            return DEFAULT_NAMESPACE


class ConfigMap(NamespacedAPIObject):

    version = "v1"
    endpoint = "configmaps"
    kind = "ConfigMap"


class DaemonSet(NamespacedAPIObject):

    version = "extensions/v1beta1"
    endpoint = "daemonsets"
    kind = "DaemonSet"


class Deployment(NamespacedAPIObject, ReplicatedMixin, ScalableMixin):

    version = "extensions/v1beta1"
    endpoint = "deployments"
    kind = "Deployment"

    @property
    def ready(self):
        return (
            self.obj["status"]["observedGeneration"] >= self.obj["metadata"]["generation"] and
            self.obj["status"]["updatedReplicas"] == self.replicas
        )


class Endpoint(NamespacedAPIObject):

    version = "v1"
    endpoint = "endpoints"
    kind = "Endpoint"


class Event(NamespacedAPIObject):

    version = "v1"
    endpoint = "events"
    kind = "Event"


class ResourceQuota(NamespacedAPIObject):

    version = "v1"
    endpoint = "resourcequotas"
    kind = "ResourceQuota"


class ServiceAccount(NamespacedAPIObject):

    version = "v1"
    endpoint = "serviceaccounts"
    kind = "ServiceAccount"


class Ingress(NamespacedAPIObject):

    version = "extensions/v1beta1"
    endpoint = "ingresses"
    kind = "Ingress"


class ThirdPartyResource(APIObject):

    version = "extensions/v1beta1"
    endpoint = "thirdpartyresources"
    kind = "ThirdPartyResource"


class Job(NamespacedAPIObject, ScalableMixin):

    version = "batch/v1"
    endpoint = "jobs"
    kind = "Job"
    scalable_attr = "parallelism"

    @property
    def parallelism(self):
        return self.obj["spec"]["parallelism"]

    @parallelism.setter
    def parallelism(self, value):
        self.obj["spec"]["parallelism"] = value


class Namespace(APIObject):

    version = "v1"
    endpoint = "namespaces"
    kind = "Namespace"


class Node(APIObject):

    version = "v1"
    endpoint = "nodes"
    kind = "Node"

    @property
    def unschedulable(self):
        if 'unschedulable' in self.obj["spec"]:
            return self.obj["spec"]["unschedulable"]
        return False

    @unschedulable.setter
    def unschedulable(self, value):
        self.obj["spec"]["unschedulable"] = value
        self.update()

    def cordon(self):
        self.unschedulable = True

    def uncordon(self):
        self.unschedulable = False


class Pod(NamespacedAPIObject):

    version = "v1"
    endpoint = "pods"
    kind = "Pod"

    @property
    def ready(self):
        cs = self.obj["status"].get("conditions", [])
        condition = next((c for c in cs if c["type"] == "Ready"), None)
        return condition is not None and condition["status"] == "True"

    def logs(self, container=None, pretty=None, previous=False,
             since_seconds=None, since_time=None, timestamps=False,
             tail_lines=None, limit_bytes=None):
        """
        Produces the same result as calling kubectl logs pod/<pod-name>.
        Check parameters meaning at
        http://kubernetes.io/docs/api-reference/v1/operations/,
        part 'read log of the specified Pod'. The result is plain text.
        """
        log_call = "log"
        params = {}
        if container is not None:
            params["container"] = container
        if pretty is not None:
            params["pretty"] = pretty
        if previous:
            params["previous"] = "true"
        if since_seconds is not None and since_time is None:
            params["sinceSeconds"] = int(since_seconds)
        elif since_time is not None and since_seconds is None:
            params["sinceTime"] = since_time
        if timestamps:
            params["timestamps"] = "true"
        if tail_lines is not None:
            params["tailLines"] = int(tail_lines)
        if limit_bytes is not None:
            params["limitBytes"] = int(limit_bytes)

        query_string = urlencode(params)
        log_call += "?{}".format(query_string) if query_string else ""
        kwargs = {
            "version": self.version,
            "namespace": self.namespace,
            "operation": log_call,
        }
        r = self.api.get(**self.api_kwargs(**kwargs))
        r.raise_for_status()
        return r.text


class ReplicationController(NamespacedAPIObject, ReplicatedMixin, ScalableMixin):

    version = "v1"
    endpoint = "replicationcontrollers"
    kind = "ReplicationController"


class ReplicaSet(NamespacedAPIObject, ReplicatedMixin, ScalableMixin):

    version = "extensions/v1beta1"
    endpoint = "replicasets"
    kind = "ReplicaSet"


class Secret(NamespacedAPIObject):

    version = "v1"
    endpoint = "secrets"
    kind = "Secret"


class Service(NamespacedAPIObject):

    version = "v1"
    endpoint = "services"
    kind = "Service"


class PersistentVolume(APIObject):

    version = "v1"
    endpoint = "persistentvolumes"
    kind = "PersistentVolume"


class PersistentVolumeClaim(NamespacedAPIObject):

    version = "v1"
    endpoint = "persistentvolumeclaims"
    kind = "PersistentVolumeClaim"


class HorizontalPodAutoscaler(NamespacedAPIObject):

    version = "autoscaling/v1"
    endpoint = "horizontalpodautoscalers"
    kind = "HorizontalPodAutoscaler"


class PetSet(NamespacedAPIObject):

    version = "apps/v1alpha1"
    endpoint = "petsets"
    kind = "PetSet"


class Role(NamespacedAPIObject):

    version = "rbac.authorization.k8s.io/v1"
    endpoint = "roles"
    kind = "Role"


class RoleBinding(NamespacedAPIObject):

    version = "rbac.authorization.k8s.io/v1"
    endpoint = "rolebindings"
    kind = "RoleBinding"


class ClusterRole(APIObject):

    version = "rbac.authorization.k8s.io/v1"
    endpoint = "clusterroles"
    kind = "ClusterRole"


class ClusterRoleBinding(APIObject):

    version = "rbac.authorization.k8s.io/v1"
    endpoint = "clusterrolebindings"
    kind = "ClusterRoleBinding"
