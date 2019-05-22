#=========================================================================
# OpenLoopCLPass.py
#=========================================================================
# Generate a simple schedule (no Mamba techniques here) based on the
# DAG generated by some previous pass.
#
# Author : Shunning Jiang
# Date   : Apr 20, 2019

from BasePass     import BasePass, PassMetadata
from collections  import deque
from graphviz     import Digraph
from errors import PassOrderError
from pymtl.dsl.errors import UpblkCyclicError
from pymtl import *

class OpenLoopCLPass( BasePass ):
  def __call__( self, top ):
    if not hasattr( top._dag, "all_constraints" ):
      raise PassOrderError( "all_constraints" )

    top._sched = PassMetadata()

    self.schedule_with_top_level_callee( top )

  def schedule_with_top_level_callee( self, top ):

    # Construct the graph with top level callee port

    V = top.get_all_update_blocks() | top._dag.genblks

    for x in top._dag.top_level_callee_ports:
      try:
        if not x._dsl.is_guard:
          V.add(x)
      except AttributeError:
        V.add(x)

    E   = top._dag.all_constraints
    Es  = { v: [] for v in V }
    InD = { v: 0  for v in V }

    for (u, v) in E: # u -> v
      InD[v] += 1
      Es [u].append( v )

    method_guard_mapping = {}

    for (x, y) in top._dag.top_level_callee_constraints:

      # Use the actual method object for constraints

      xx = x
      try:
        if x.guarded_ifc:
          xx = x.method
          method_guard_mapping[xx] = x.get_guard()
      except AttributeError:
        pass

      yy = y
      try:
        if y.guarded_ifc:
          yy = y.method
          method_guard_mapping[yy] = y.get_guard()
      except AttributeError:
        pass

      InD[yy] += 1
      Es [xx].append( yy )
      E.add( (xx, yy) )

    # Perform topological sort for a serial schedule.

    schedule = []

    Q = deque( [ v for v in V if not InD[v] ] )

    while Q:
      import random
      random.shuffle(Q)
      u = Q.pop()
      if u in method_guard_mapping:
        schedule.append( method_guard_mapping[ u ] )
      schedule.append( u )
      for v in Es[u]:
        InD[v] -= 1
        if not InD[v]:
          Q.append( v )

    top._sched.new_schedule_index  = 0
    top._sched.orig_schedule_index = 0
    
    # ================ FIXME ====================
    # Line trace stuffs should go in wrapper class
    # when constraint is fixed
    def wrap_line_trace( top, f ):
      top.line_trace_string = ""
      def new_line_trace():
        line_trace_string = top.line_trace_string
        top.line_trace_string = ""
        return f() + " " + line_trace_string

      return new_line_trace

    setattr( top, "line_trace", wrap_line_trace( top, top.line_trace ) )


    def wrap_method_trace( top, name, method ):
      top.line_trace_string = ""
      def wrapped_method( **kwargs ):
        line_trace = " " + name + "(" + ", ".join([
          "{arg}={value}".format( arg=arg, value=value )
          for arg, value in kwargs.iteritems()
      ] ) + ")"
        ret = method(**kwargs)

        if not ret is None:
          line_trace += " -> " + str(ret)

        top.line_trace_string += " " + line_trace
        
        return ret
      return wrapped_method
    # ================= end ===================

    def wrap_method( top, method,
                     my_idx_new, next_idx_new, schedule_no_method,
                     my_idx_orig, next_idx_orig ):
      #  print "new", my_idx_new, next_idx_new, "orig", my_idx_orig, next_idx_orig

      def actual_method( *args, **kwargs ):
        i = top._sched.new_schedule_index
        j = top._sched.orig_schedule_index

        if j > my_idx_orig:
          # This means we need to advance the current cycle to the end
          # and then normally execute until we get to the same point.
          # We use original schedule index to handle the case where
          # there are two consecutive methods.
          while i < len(schedule_no_method):
            schedule_no_method[i]()
            i += 1
          i = j = 0
          top.num_cycles_executed += 1

        # We advance from the current point i to the method's position in
        # the schedule without method just to execute those blocks
        while i < my_idx_new:
          schedule_no_method[i]()
          i += 1

        # Execute the method
        ret = method( *args, **kwargs )

        # Execute all update blocks before the next method. Note that if
        # there are several consecutive methods, my_idx_new is equal to next_idx_new
        while i < next_idx_new:
          schedule_no_method[i]()
          i += 1
        j = next_idx_orig

        if i == len(schedule_no_method):
          i = j = 0
          top.num_cycles_executed += 1

        top._sched.new_schedule_index = i
        top._sched.orig_schedule_index = j
        return ret

      return actual_method

    # Here we are trying to avoid scanning the original schedule that
    # contains methods because we will need isinstance in that case.
    # As a result we created a preprocessed list for execution and use
    # the dictionary to look up the new index of functions.

    # The last element is always line trace
    def print_line_trace():
      # ================ FIXME ====================
      # Not needed when method trace is moved?
      try:
        if top.hide_line_trace:
          return
      except AttributeError:
        pass
      # ================= end ===================
      print top.line_trace()
    schedule.append( print_line_trace )

    schedule_no_method = [ x for x in schedule if not isinstance(x, CalleePort) ]
    mapping = { x : i for i, x in enumerate( schedule_no_method ) }

    #  print "new"
    #  for i, x in enumerate(schedule_no_method):
      #  print i, x
    #  print
    #  print "orig"
    #  for i, x in enumerate(schedule):
      #  print i, x

    for i, x in enumerate( schedule ):
      if isinstance( x, CalleePort ):
        x.original_method = x.method

        # This is to find the next non-method block's position in the
        # original schedule
        next_func   = i + 1
        while next_func < len(schedule):
          if not isinstance( schedule[next_func], CalleePort ):
            break
          next_func += 1

        # Get the index of the block in the schedule without method
        # This always exists because we append a line trace at the end
        map_next_func = mapping[ schedule[next_func] ]

        # Get the index of the next method in the schedule without method
        next_method = i + 1
        while next_method < len(schedule):
          if isinstance( schedule[next_method], CalleePort ):
            break
          next_method += 1

        # If there is another method after me, I calculate the range of
        # blocks that I need to call and then stop before the user calls
        # the next method.
        if next_method < len(schedule):
          next_func = next_method
          while next_func < len(schedule):
            if not isinstance( schedule[next_func], CalleePort ):
              break
            next_func += 1
          # Get the index in the compacted schedule
          map_next_func_of_next_method = mapping[ schedule[next_func] ]
        else:
          map_next_func_of_next_method = len(schedule_no_method)

        # ================ FIXME ====================
        # Line trace stuffs should go in wrapper class
        # when constraint is fixed
        try:
          if not x._dsl.is_guard:
            x.method = wrap_method_trace( top, x.method.__name__, x.method )
        except AttributeError:
          pass
        # ================ end ====================

        x.method = wrap_method( top, x.method,
                                map_next_func, map_next_func_of_next_method,
                                schedule_no_method,
                                i, next_method )

                     #  my_idx_new, next_idx_new, schedule_no_method,
                     #  my_idx_orig, next_idx_orig ):
    top.num_cycles_executed = 0

    #  from graphviz import Digraph
    #  dot = Digraph()
    #  dot.graph_attr["rank"] = "same"
    #  dot.graph_attr["ratio"] = "compress"
    #  dot.graph_attr["margin"] = "0.1"

    #  for x in V:
      #  x_name = repr(x) if isinstance( x, CalleePort ) else x.__name__
      #  try:
        #  x_host = repr(x.get_parent_object() if isinstance( x, CalleePort )
                      #  else top.get_update_block_host_component(x))
      #  except:
        #  x_host = ""
      #  dot.node( x_name +"\\n@" + x_host, shape="box")

    #  for (x, y) in E:
      #  x_name = repr(x) if isinstance( x, CalleePort ) else x.__name__
      #  try:
        #  x_host = repr(x.get_parent_object() if isinstance( x, CalleePort )
                      #  else top.get_update_block_host_component(x))
      #  except:
        #  x_host = ""
      #  y_name = repr(y) if isinstance( y, CalleePort ) else y.__name__
      #  try:
        #  y_host = repr(y.get_parent_object() if isinstance( y, CalleePort )
                      #  else top.get_update_block_host_component(y))
      #  except:
        #  y_host = ""

      #  dot.edge( x_name+"\\n@"+x_host, y_name+"\\n@"+y_host )
    #  dot.render( "/tmp/upblk-dag.gv", view=True )

    return schedule

