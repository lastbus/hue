#!/usr/bin/env python
# Licensed to Cloudera, Inc. under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  Cloudera, Inc. licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging

from django.utils.translation import ugettext as _

from jobbrowser.apis.base_api import Api, MockDjangoRequest, _extract_query_params
from jobbrowser.views import job_attempt_logs_json, kill_job


LOG = logging.getLogger(__name__)


try:
  from jobbrowser.api import YarnApi as NativeYarnApi
except Exception, e:
  LOG.exception('Some application are not enabled: %s' % e)


class JobApi(Api):

  def __init__(self, user):
    self.user = user
    self.yarn_api = YarnApi(user) # TODO: actually long term move job aggregations to the frontend instead probably
    self.impala_api = ImpalaApi(user)
    self.request = None

  def apps(self, filters):
    jobs = self.yarn_api.apps(filters)
    # += Impala
    # += Sqoop2
    return jobs

  def app(self, appid):
    return self._get_api(appid).app(appid)

  def action(self, appid, operation):
    return self._get_api(appid).action(operation, appid)

  def logs(self, appid, app_type, log_name):
    return self._get_api(appid).logs(appid, app_type, log_name)

  def profile(self, appid, app_type, app_property):
    return self._get_api(appid).profile(appid, app_type, app_property)

  def _get_api(self, appid):
    if appid.startswith('task_'):
      return YarnMapReduceTaskApi(self.user, appid)
    elif appid.startswith('attempt_'):
      return YarnMapReduceTaskAttemptApi(self.user, appid)
    else:
      return self.yarn_api # application_

  def _set_request(self, request):
    self.request = request


class YarnApi(Api):
  """YARN, MR, Spark"""

  def apps(self, filters):
    filter_params = {
      'user': self.user,
      'username': '',
      'text': '',
      'state': 'all'
    }

    filter_params.update(_extract_query_params(filters))

    if 'time' in filters:
      filter_params['time_value'] = int(filters['time']['time_value'])
      filter_params['time_unit'] = filters['time']['time_unit']

    jobs = NativeYarnApi(self.user).get_jobs(**filter_params)

    return [{
        'id': app.jobId,
        'name': app.name,
        'type': app.applicationType,
        'status': app.status,
        'apiStatus': self._api_status(app.status),
        'user': self.user.username,
        'progress': app.progress,
        'duration': 10 * 3600,
        'submitted': 10 * 3600
    } for app in jobs]

  def app(self, appid):
    app = NativeYarnApi(self.user).get_job(jobid=appid)

    common = {
        'id': app.jobId,
        'name': app.name,
        'type': app.applicationType,
        'status': app.status,
        'apiStatus': self._api_status(app.status),
        'user': self.user.username,
        'progress': app.progress,
        'duration': 10 * 3600,
        'submitted': 10 * 3600
    }

    if app.applicationType == 'MR2':
      common['type'] = 'MAPREDUCE'
      common['duration'] = app.duration
      common['durationFormatted'] = app.durationFormatted

      common['properties'] = {
          'maps_percent_complete': app.maps_percent_complete,
          'reduces_percent_complete': app.reduces_percent_complete,
          'finishedMaps': app.finishedMaps,
          'finishedReduces': app.finishedReduces,
          'desiredMaps': app.desiredMaps,
          'desiredReduces': app.desiredReduces,

          'tasks': [],
          'metadata': [],
          'counters': []
      }
    return common


  def action(self, operation, appid):
    if operation['action'] == 'kill':
      return kill_job(MockDjangoRequest(self.user), job=appid)
    else:
      return {}


  def logs(self, appid, app_type, log_name):
    if log_name == 'default':
      log_name = 'syslog'

    if app_type == 'MAPREDUCE':
      response = job_attempt_logs_json(MockDjangoRequest(self.user), job=appid, name=log_name)
      logs = json.loads(response.content)['log']
    else:
      logs = None
    return {'logs': logs}


  def profile(self, appid, app_type, app_property):
    if app_type == 'MAPREDUCE':
      if app_property == 'tasks':
        return {
          'task_list': YarnMapReduceTaskApi(self.user, appid).apps(),
        }
      elif app_property == 'metadata':
        return NativeYarnApi(self.user).get_job(jobid=appid).full_job_conf
      elif app_property == 'counters':
        return NativeYarnApi(self.user).get_job(jobid=appid).counters

    return {}

  def _api_status(self, status):
    if status in ['NEW', 'NEW_SAVING', 'SUBMITTED', 'ACCEPTED', 'RUNNING']:
      return 'RUNNING'
    else:
      return 'FINISHED' # FINISHED, FAILED, KILLED


class YarnMapReduceTaskApi(Api):

  def __init__(self, user, app_id):
    Api.__init__(self, user)
    self.app_id = '_'.join(app_id.replace('task_', 'application_').split('_')[:3])


  def apps(self):
    return [self._massage_task(task) for task in NativeYarnApi(self.user).get_tasks(jobid=self.app_id, pagenum=1)]


  def app(self, appid):
    task = NativeYarnApi(self.user).get_task(jobid=self.app_id, task_id=appid)

    common = self._massage_task(task)
    common['properties'] = {
      'attempts': [],
      'metadata': [],
      'counters': []
    }
    common['properties'].update(self._massage_task(task))

    return common


  def logs(self, appid, app_type, log_name):
    response = job_attempt_logs_json(MockDjangoRequest(self.user), job=self.app_id, name=log_name)
    logs = json.loads(response.content)['log']

    return {'progress': 0, 'logs': logs}


  def profile(self, appid, app_type, app_property):
    if app_property == 'attempts':
      return {
          'task_list': YarnMapReduceTaskAttemptApi(self.user, appid).apps(),
      }
    elif app_property == 'counters':
      return NativeYarnApi(self.user).get_task(jobid=self.app_id, task_id=appid).counters

    return {}

  def _massage_task(self, task):
    return {
        'id': task.id,
        'type': task.type,
        'elapsedTime': task.elapsedTime,
        'progress': task.progress,
        'state': task.state,
        'startTime': task.startTime,
        'successfulAttempt': task.successfulAttempt,
        'finishTime': task.finishTime
    }


class YarnMapReduceTaskAttemptApi(Api):

  def __init__(self, user, app_id):
    Api.__init__(self, user)
    self.app_id = '_'.join(app_id.replace('task_', 'application_').replace('attempt_', 'application_').split('_')[:3])
    self.task_id = '_'.join(app_id.replace('attempt_', 'task_').split('_')[:5])
    self.attempt_id = app_id

  def apps(self):
    return [self._massage_task(task) for task in NativeYarnApi(self.user).get_task(jobid=self.app_id, task_id=self.task_id).attempts]


  def app(self, appid):
    task = NativeYarnApi(self.user).get_task(jobid=self.app_id, task_id=self.task_id).get_attempt(self.attempt_id)

    common = self._massage_task(task)
    common['properties'] = {
        'metadata': [],
        'counters': []
    }
    common['properties'].update(self._massage_task(task))

    return common


  def logs(self, appid, app_type, log_name):
    task = NativeYarnApi(self.user).get_task(jobid=self.app_id, task_id=self.task_id).get_attempt(self.attempt_id)
    stdout, stderr, syslog = task.get_task_log()

    return {'progress': 0, 'logs': syslog if log_name == 'syslog' else stderr if log_name == 'stderr' else stdout}


  def profile(self, appid, app_type, app_property):
    if app_property == 'counters':
      return NativeYarnApi(self.user).get_task(jobid=self.app_id, task_id=self.task_id).get_attempt(self.attempt_id).counters

    return {}

  def _massage_task(self, task):
    return {
        #"elapsedMergeTime" : task.elapsedMergeTime,
        #"shuffleFinishTime" : task.shuffleFinishTime,
        "assignedContainerId" : task.assignedContainerId,
        "progress" : task.progress,
        "elapsedTime" : task.elapsedTime,
        "state" : task.state,
        #"elapsedShuffleTime" : task.elapsedShuffleTime,
        #"mergeFinishTime" : task.mergeFinishTime,
        "rack" : task.rack,
        #"elapsedReduceTime" : task.elapsedReduceTime,
        "nodeHttpAddress" : task.nodeHttpAddress,
        "type" : task.type + '_ATTEMPT',
        "startTime" : task.startTime,
        "id" : task.id,
        "finishTime" : task.finishTime
    }


class YarnAtsApi(Api):
  pass


class ImpalaApi(Api):
  pass


class Sqoop2Api(Api):
  pass
