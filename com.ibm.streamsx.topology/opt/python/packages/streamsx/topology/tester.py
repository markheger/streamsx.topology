import streamsx.ec as ec
import streamsx.topology.context
import os
import unittest
import logging

_logger = logging.getLogger('streamsx.topology.test')



class Tester(object):
    """Testing support for a Topology.

    Allows testing of a Topology by creating conditions against the contents
    of its streams.

    Conditions may be added to a topoogy at any time before submission.

    If a topology is submitted directly to a context then the graph
    is not modified. This allows testing code to be inserted while
    the topology is being built, but not acted upon unless the topology
    is submitted in test mode.

    If a topology is submitted through the test method then the topology
    may be modified to include operations to ensure the conditions are met.

    Args:
        topology: Topology to be tested.
    """
    def __init__(self, topology):
       self.topology = topology
       topology.tester = self
       self._conditions = {}

    @staticmethod
    def setup_standalone(test):
        """
        Setup a test case to run tests using IBM Streams standalone mode.

        Two attributes are set in the test case:
         * test_ctxtype - Context type the test will be run in.
         * test_config- Test configuration.

        Args:
            test(unittest.TestCase): Test case to be set up to run tests using Tester

        Returns: None
        """
        test.test_ctxtype = "STANDALONE"
        test.test_config = {}

    @staticmethod
    def setup_distributed(test):
        """
        Setup a test case to run tests using IBM Streams distributed mode.

        Two attributes are set in the test case:
         * test_ctxtype - Context type the test will be run in.
         * test_config- Test configuration.

        Args:
            test(unittest.TestCase): Test case to be set up to run tests using Tester

        Returns: None
        """
        test.test_ctxtype = "DISTRIBUTED"
        test.test_config = {}

    def setup_streaming_analytics(test, service_name=None, force_remote_build=False):
        """
        Setup a test case to run tests using Streaming Analytics service on IBM Bluemix cloud platform.


        Two attributes are set in the test case:
         * test_ctxtype - Context type the test will be run in.
         * test_config- Test configuration.

        Args:
            test(unittest.TestCase): Test case to be set up to run tests using Tester

        Returns: None
        """
        test.test_ctxtype = "ANALYTICS_SERVICE"
        if service_name is None:
            service_name = os.environ.get('STREAMS_SERVICE_NAME', None)
        if service_name is None:
            raise ValueError("Service name not set.")
        test.test_config = {'topology.service.name': service_name}
        if force_remote_build:
            test.test_config['topology.forceRemoteBuild'] = True

    def add_condition(self, stream, condition):
        self._conditions[condition.name] = (stream, condition)
        return stream

    def tuple_count(self, stream, count):
        """Test that that a stream returns an exact number of tuples.

        Args:
            stream(Stream): Stream to be tested.
            count: Number of tuples expected.

        Returns: stream

        """
        _logger.debug("Adding tuple count (%d) condition to stream %s.", count, stream)
        name = "ExactCount" + str(len(self._conditions));
        cond = TupleExactCount(count, name)
        return self.add_condition(stream, cond)

    def contents(self, stream, expected):
        """Test that a stream contains the expected tuples.

        Args:
            stream(Stream): Stream to be tested.
            expected(list): List of expected tuples.

        Returns:

        """
        name = "StreamContents" + str(len(self._conditions));
        cond = StreamContents(expected, name)
        return self.add_condition(stream, cond)

    def test(self, ctxtype, config=None, assert_on_fail=True):
        """Test the topology.

        Submits the topology for testing and verifies the test conditions are met.

        Args:
            ctxtype(str): Context type for submission.
            config: Configuration for submission.
            assert_on_fail(bool): True to raise an assertion if the test fails, False to return the passed status.

        Returns:
            bool: True if test passed, False if test failed.

        """

        # Add the conditions into the graph as sink operators
        _logger.debug("Adding conditions to topology %s.", self.topology.name)
        for ct in self._conditions.values():
            condition = ct[1]
            stream = ct[0]
            stream.for_each(condition, name=condition.name)

        if config is None:
            config = {}

        _logger.debug("Starting test topology %s context %s.", self.topology.name, ctxtype)

        if "STANDALONE" == ctxtype:
            passed = self._standalone_test(config)
        elif "DISTRIBUTED" == ctxtype:
            passed = self._distributed_test(config)
        elif "ANALYTICS_SERVICE" == ctxtype:
            passed = self._streaming_analytics_test(config)
        else:
            raise NotImplementedError("Tester context type not implemented:", ctxtype)

        if assert_on_fail:
            assert passed, "Test failed for topology: " + self.topology.name
        if passed:
            _logger.info("Test topology %s passed for context:%s", self.topology.name, ctxtype)
        else:
            _logger.error("Test topology %s failed for context:%s", self.topology.name, ctxtype)

    def _standalone_test(self, config):
        """ Test using STANDALONE.
        Success is soley indicated by the process completing and returning zero.
        """
        sr = streamsx.topology.context.submit("STANDALONE", self.topology, config)
        self.result = {'passed': sr['return_code'], 'submission_result': sr}
        return sr['return_code'] == 0

    def _distributed_test(self, config):

        sjr = streamsx.topology.context.submit("DISTRIBUTED", self.topology, config)
        if sjr['return_code'] != 0:
            print("DO AS LOGGER", "Failed to submit job to distributed instance.")
            return False
        sc = StreamsConnection()
        return self._distributed_wait_for_result(sc, sjr)

    def _streaming_analytics_test(self, config):
        sjr = streamsx.topology.context.submit("ANALYTICS_SERVICE", self.topology, config)
        sc = StreamsConnection(config=config)
        return self._distributed_wait_for_result(sc, sjr)

    def _distributed_wait_for_result(self, sc, sjr):
        cc = _ConditionChecker(self, sc, sjr)
        self.result = cc._complete()
        self.result['submission_result'] = sjr
        return self.result['passed']

class Condition(object):
    _METRIC_PREFIX = "streamsx.condition:"

    @staticmethod
    def _mn(mt, name):
        return Condition._METRIC_PREFIX + mt + ":" + name

    def __init__(self, name=None):
        self.name = name
        self._valid = False
        self._fail = False
    @property
    def valid(self):
        return self._valid
    @valid.setter
    def valid(self, v):
        if self._fail:
           return None
        if self._valid != v:
            if v:
                self._metric_valid.value = 1
            else:
                self._metric_valid.value = 0
            self._valid = v
        self._metric_seq += 1

    def fail(self):
        self._metric_fail.value = 1
        self.valid = False
        self._fail = True
        if (ec.is_standalone()):
            raise AssertionError("Condition failed:" + str(self))

    def __getstate__(self):
        # Remove metrics from saved state.
        state = self.__dict__.copy()
        for key in state:
            if key.startswith('_metric'):
              del state[key]
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)

    def __enter__(self):
        self._metric_valid = self._create_metric("valid", kind='Gauge')
        self._metric_seq = self._create_metric("seq")
        self._metric_fail = self._create_metric("fail", kind='Gauge')
        pass

    def __exit__(self, exc_type, exc_value, traceback):
        if (ec.is_standalone()):
            if not self._fail and not self.valid:
                raise AssertionError("Condition failed:" + str(self))

    def _create_metric(self, mt, kind=None):
        return ec.CustomMetric(self, name=Condition._mn(mt, self.name), kind=kind)


class TupleExactCount(Condition):
    def __init__(self, target, name=None):
        super(TupleExactCount, self).__init__(name)
        self.target = target
        self.count = 0
        if target == 0:
            self.valid = True

    def __call__(self, tuple):
        self.count += 1
        self.valid = self.target == self.count
        if self.count > self.target:
            self.fail()

    def __str__(self):
        return "Exact tuple count: expected:" + str(self.target) + " received:" + str(self.count)


class StreamContents(Condition):
    def __init__(self, expected, name=None):
        super(StreamContents, self).__init__(name)
        self.expected = expected
        self.received = []

    def __call__(self, tuple):
        self.received.append(tuple)
        if len(self.received) > len(self.expected):
            self.fail()
            return None

        if self.expected[len(self.received) - 1] != self.received[-1]:
            self.fail()
            return None

        self.valid = len(self.received) == len(self.expected)

    def __str__(self):
        return "Stream contents: expected:" + str(self.expected) + " received:" + str(self.received)


#######################################
# Internal functions
#######################################

from streamsx.rest import StreamsConnection
import time


def _result_to_dict(passed, t):
    result = {}
    result['passed'] = passed
    result['valid'] = t[0]
    result['fail'] = t[1]
    result['progress'] = t[2]
    result['conditions'] = t[3]
    return result

class _ConditionChecker(object):
    def __init__(self, tester, sc, sjr):
        self.tester = tester
        self._sc = sc
        self._sjr = sjr
        self.job_id = job_id = sjr['jobId']
        self._sequences = {}
        for cn in tester._conditions:
            self._sequences[cn] = -1
        self.delay = 0.5
        self.timeout = 10.0
        self.waits = 0
        self.additional_checks = 2

        self.job = self._find_job()

    def _complete(self):
        while (self.waits * self.delay) < self.timeout:
            check = self. __check_once()
            if check[1]:
                return self._end(False, check)
            if check[0]:
                if self.additional_checks == 0:
                    return self._end(True, check)
                self.additional_checks -= 1
                continue
            if check[2]:
                self.waits = 0
            else:
                self.waits += 1
            time.sleep(self.delay)
        return self._end(False, check)

    def _end(self, passed, check):
        result = _result_to_dict(passed, check)
        if self.job is not None:
            self.job.cancel(force= not passed)
        return result

    def __check_once(self):
        cms = self._get_job_metrics()
        valid = True
        progress = True
        fail = False
        condition_states = {}
        for cn in self._sequences:
            condition_states[cn] = 'NotValid'
            seq_mn = Condition._mn('seq', cn)
            # If the metrics are missing then the operator
            # is probably still starting up, cannot be valid.
            if not seq_mn in cms:
                valid = False
                continue
            seq_m = cms[seq_mn]
            if seq_m.value == self._sequences[cn]:
                progress = False
            else:
                self._sequences[cn] = seq_m.value

            fail_mn = Condition._mn('fail', cn)
            if not fail_mn in cms:
                valid = False
                continue

            fail_m = cms[fail_mn]
            if fail_m.value != 0:
                fail = True
                condition_states[cn] = 'Fail'
                continue

            valid_mn =  Condition._mn('valid', cn)

            if not valid_mn in cms:
                valid = False
                continue
            valid_m = cms[valid_mn]

            if valid_m.value == 0:
                valid = False
            else:
                condition_states[cn] = 'Valid'

        return (valid, fail, progress, condition_states)

    def _find_job(self):
        for instance in self._sc.get_instances(id=self._sc.instance_id):
            jobs = instance.get_jobs(id=self.job_id)
            if len(jobs) == 1:
                return jobs[0]
            raise AssertionError("Job not found:job_id:", self.job_id)
        raise AssertionError("Instance not found:", self._sc.instance_id)

    def _get_job_metrics(self):
        """Fetch all the condition metrics for a job.
        We refetch the metrics each time to ensure that we don't miss
        any being added, e.g. if an operator is slow to start.
        """
        cms = {}
        for op in self.job.get_operators():
            metrics = op.get_metrics(name=Condition._METRIC_PREFIX + '*')
            for m in metrics:
                cms[m.name] = m
        return cms