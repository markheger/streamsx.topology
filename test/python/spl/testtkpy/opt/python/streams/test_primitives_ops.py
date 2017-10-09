# Licensed Materials - Property of IBM
# Copyright IBM Corp. 2017

# Import the SPL decorators
from streamsx.spl import spl

#------------------------------------------------------------------
# Test passing in SPL types functions
#------------------------------------------------------------------

# Defines the SPL namespace for any functions in this module
# Multiple modules can map to the same namespace
def spl_namespace():
    return "com.ibm.streamsx.topology.pytest.pyprimitives"

@spl.primitive_operator()
class NoPorts(object):
    def __init__(self, mn, iv):
        self.mn = mn
        self.iv = iv

    def __enter__(self):
        import streamsx.ec as ec
        self.cm = ec.CustomMetric(self, 'NP_' + self.mn)
        self.cm.value = self.iv

    def __exit__(self, exc_type, exc_value, traceback):
        pass
   
@spl.primitive_operator()
class SingleInputPort(object):
    def __init__(self):
        pass

    def __enter__(self):
        import streamsx.ec as ec
        self.cm = ec.CustomMetric(self, 'SIP_METRIC')

    def __exit__(self, exc_type, exc_value, traceback):
        pass

    @spl.input_port()
    def my_only_port(self, *t):
        print("SELF", self, flush=True)
        print("tuple", t, flush=True)
        self.cm.value = t[0]
