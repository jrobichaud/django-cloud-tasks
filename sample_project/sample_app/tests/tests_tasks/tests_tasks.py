import json
from datetime import timedelta, datetime, UTC
from unittest.mock import patch

from django.apps import apps
from django.test import SimpleTestCase, TestCase
from django.utils.timezone import now
from freezegun import freeze_time
from gcp_pilot.exceptions import DeletedRecently
from gcp_pilot.mocker import patch_auth

from django_cloud_tasks import exceptions
from django_cloud_tasks.tasks import Task, TaskMetadata
from django_cloud_tasks.tests import tests_base
from django_cloud_tasks.tests.tests_base import eager_tasks
from sample_app import tasks


class TasksTest(SimpleTestCase):
    def setUp(self):
        super().setUp()
        Task._get_tasks_client.cache_clear()

        patch_output = patch("django_cloud_tasks.tasks.TaskMetadata.from_task_obj")
        patch_output.start()
        self.addCleanup(patch_output.stop)

        auth = patch_auth()
        auth.start()
        self.addCleanup(auth.stop)

    def tearDown(self):
        super().tearDown()
        Task._get_tasks_client.cache_clear()

    def patch_push(self, **kwargs):
        return patch("gcp_pilot.tasks.CloudTasks.push", **kwargs)

    @property
    def app_config(self):
        return apps.get_app_config("django_cloud_tasks")

    def test_registered_tasks(self):
        expected_tasks = {
            "CalculatePriceTask",
            "FailMiserablyTask",
            "OneBigDedicatedTask",
            "RoutineExecutorTask",
            "SayHelloTask",
            "SayHelloWithParamsTask",
            "DummyRoutineTask",
            "RoutineReverterTask",
            "ParentCallingChildTask",
            "ExposeCustomHeadersTask",
            "PublishPersonTask",
        }
        self.assertEqual(expected_tasks, set(self.app_config.on_demand_tasks))

        expected_tasks = {"SaySomethingTask"}
        self.assertEqual(expected_tasks, set(self.app_config.periodic_tasks))

        expected_tasks = {"PleaseNotifyMeTask", "ParentSubscriberTask"}
        self.assertEqual(expected_tasks, set(self.app_config.subscriber_tasks))

    def test_get_task(self):
        received_task = self.app_config.get_task(name="SayHelloWithParamsTask")
        expected_task = tasks.SayHelloWithParamsTask
        self.assertEqual(expected_task, received_task)

    def test_get_abstract_task(self):
        with self.assertRaises(expected_exception=exceptions.TaskNotFound):
            self.app_config.get_task(name="PublisherTask")

    def test_get_task_not_found(self):
        with self.assertRaises(exceptions.TaskNotFound):
            self.app_config.get_task(name="PotatoTask")

    def test_task_async(self):
        with (
            patch_auth(),
            self.patch_push() as push,
        ):
            tasks.CalculatePriceTask.asap(price=30, quantity=4, discount=0.2)

        expected_call = dict(
            queue_name="tasks",
            url="http://localhost:8080/tasks/CalculatePriceTask",
            payload=json.dumps({"price": 30, "quantity": 4, "discount": 0.2}),
            headers={"X-CloudTasks-Projectname": "potato-dev"},
        )
        push.assert_called_once_with(**expected_call)

    def test_task_async_only_once(self):
        with self.patch_push() as push:
            tasks.FailMiserablyTask.asap(magic_number=666)

        expected_call = dict(
            task_name="FailMiserablyTask",
            queue_name="tasks",
            url="http://localhost:8080/tasks/FailMiserablyTask",
            payload=json.dumps({"magic_number": 666}),
            unique=False,
            headers={"X-CloudTasks-Projectname": "potato-dev"},
        )
        push.assert_called_once_with(**expected_call)

    def test_task_async_reused_queue(self):
        effects = [DeletedRecently("Queue tasks"), None]
        with self.patch_push(side_effect=effects) as push:
            tasks.CalculatePriceTask.asap(price=30, quantity=4, discount=0.2)

        expected_call = dict(
            queue_name="tasks",
            url="http://localhost:8080/tasks/CalculatePriceTask",
            payload=json.dumps({"price": 30, "quantity": 4, "discount": 0.2}),
            headers={"X-CloudTasks-Projectname": "potato-dev"},
        )
        expected_backup_call = expected_call
        expected_backup_call["queue_name"] += "--temp"

        self.assertEqual(2, push.call_count)
        push.assert_any_call(**expected_call)
        push.assert_called_with(**expected_backup_call)

    def test_task_eager(self):
        with eager_tasks():
            response = tasks.CalculatePriceTask.asap(price=30, quantity=4, discount=0.2)
        self.assertGreater(response, 0)

    def test_task_later_int(self):
        with self.patch_push() as push:
            task_kwargs = dict(price=30, quantity=4, discount=0.2)
            tasks.CalculatePriceTask.later(eta=1800, task_kwargs=task_kwargs)

        expected_call = dict(
            delay_in_seconds=1800,
            queue_name="tasks",
            url="http://localhost:8080/tasks/CalculatePriceTask",
            payload=json.dumps({"price": 30, "quantity": 4, "discount": 0.2}),
            headers={"X-CloudTasks-Projectname": "potato-dev"},
        )
        push.assert_called_once_with(**expected_call)

    def test_task_later_delta(self):
        delta = timedelta(minutes=42)
        with self.patch_push() as push:
            task_kwargs = dict(price=30, quantity=4, discount=0.2)
            tasks.CalculatePriceTask.later(eta=delta, task_kwargs=task_kwargs)

        expected_call = dict(
            delay_in_seconds=2520,
            queue_name="tasks",
            url="http://localhost:8080/tasks/CalculatePriceTask",
            payload=json.dumps({"price": 30, "quantity": 4, "discount": 0.2}),
            headers={"X-CloudTasks-Projectname": "potato-dev"},
        )
        push.assert_called_once_with(**expected_call)

    @freeze_time("2020-01-01T00:00:00")
    def test_task_later_time(self):
        some_time = now() + timedelta(minutes=100)
        with self.patch_push() as push:
            task_kwargs = dict(price=30, quantity=4, discount=0.2)
            tasks.CalculatePriceTask.later(eta=some_time, task_kwargs=task_kwargs)

        expected_call = dict(
            delay_in_seconds=60 * 100,
            queue_name="tasks",
            url="http://localhost:8080/tasks/CalculatePriceTask",
            payload=json.dumps({"price": 30, "quantity": 4, "discount": 0.2}),
            headers={"X-CloudTasks-Projectname": "potato-dev"},
        )
        push.assert_called_once_with(**expected_call)

    def test_task_later_error(self):
        with self.patch_push() as push:
            with self.assertRaisesRegex(expected_exception=ValueError, expected_regex="Unsupported schedule"):
                task_kwargs = dict(price=30, quantity=4, discount=0.2)
                tasks.CalculatePriceTask.later(eta="potato", task_kwargs=task_kwargs)

        push.assert_not_called()

    def test_singleton_client_on_task(self):
        # we have a singleton if it calls the same task twice
        with (
            patch("django_cloud_tasks.tasks.TaskMetadata.from_task_obj"),
            patch("django_cloud_tasks.tasks.task.CloudTasks") as client,
        ):
            for _ in range(10):
                tasks.CalculatePriceTask.asap()

        client.assert_called_once_with()
        self.assertEqual(10, client().push.call_count)

    def test_singleton_client_creates_new_instance_on_new_task(self):
        with (
            patch("django_cloud_tasks.tasks.TaskMetadata.from_task_obj"),
            patch("django_cloud_tasks.tasks.task.CloudTasks") as client,
        ):
            tasks.SayHelloTask.asap()
            tasks.CalculatePriceTask.asap()

        self.assertEqual(2, client.call_count)


class SayHelloTaskTest(TestCase, tests_base.RoutineTaskTestMixin):
    @property
    def task(self):
        return tasks.SayHelloTask


class SayHelloWithParamsTaskTest(TestCase, tests_base.RoutineTaskTestMixin):
    @property
    def task(self):
        return tasks.SayHelloWithParamsTask

    @property
    def task_run_params(self):
        return {"spell": "Obliviate"}


class TestTaskMetadata(TestCase):
    some_date = datetime(1990, 7, 19, 15, 30, 42, tzinfo=UTC)

    @property
    def sample_headers(self) -> dict:
        return {
            "X-Cloudtasks-Taskexecutioncount": 7,
            "X-Cloudtasks-Taskretrycount": 1,
            "X-Cloudtasks-Tasketa": str(self.some_date.timestamp()),
            "X-Cloudtasks-Projectname": "wizard-project",
            "X-Cloudtasks-Queuename": "wizard-queue",
            "X-Cloudtasks-Taskname": "hp-1234567",
        }

    @property
    def sample_metadata(self) -> TaskMetadata:
        return TaskMetadata(
            project_id="wizard-project",
            queue_name="wizard-queue",
            task_id="hp-1234567",
            execution_number=7,
            dispatch_number=1,
            eta=self.some_date,
        )

    def test_create_from_headers(self):
        metadata = TaskMetadata.from_headers(headers=self.sample_headers)

        self.assertEqual(7, metadata.execution_number)
        self.assertEqual(1, metadata.dispatch_number)
        self.assertEqual(2, metadata.attempt_number)
        self.assertEqual(self.some_date, metadata.eta)
        self.assertEqual("wizard-project", metadata.project_id)
        self.assertEqual("wizard-queue", metadata.queue_name)
        self.assertEqual("hp-1234567", metadata.task_id)

    def test_build_headers(self):
        headers = self.sample_metadata.to_headers()

        self.assertEqual("7", headers["X-Cloudtasks-Taskexecutioncount"])
        self.assertEqual("1", headers["X-Cloudtasks-Taskretrycount"])
        self.assertEqual(str(int(self.some_date.timestamp())), headers["X-Cloudtasks-Tasketa"])
        self.assertEqual("wizard-project", headers["X-Cloudtasks-Projectname"])
        self.assertEqual("wizard-queue", headers["X-Cloudtasks-Queuename"])
        self.assertEqual("hp-1234567", headers["X-Cloudtasks-Taskname"])

    def test_comparable(self):
        reference = self.sample_metadata

        metadata_a = TaskMetadata.from_headers(self.sample_headers)
        self.assertEqual(reference, metadata_a)

        metadata_b = TaskMetadata.from_headers(self.sample_headers)
        metadata_b.execution_number += 1
        self.assertNotEqual(reference, metadata_b)

        not_metadata = True
        self.assertNotEqual(reference, not_metadata)