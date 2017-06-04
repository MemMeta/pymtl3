#-------------------------------------------------------------------------
# SimUpdateVarNetPass
#-------------------------------------------------------------------------
from pymtl.passes import SimUpdateOnlyPass, VarElaborationPass, VarConstraintPass, \
                         SignalTypeCheckPass, ScheduleUpblkPass, GenerateTickPass, \
                         SignalCleanupPass

from pymtl.components import UpdateVar

class SimUpdateVarNPass( SimUpdateOnlyPass ):

  def execute( self, m ):
    assert isinstance( m, UpdateVarNet )
    m = VarElaborationPass( dump=self.dump ).execute( m )

    m = SignalTypeCheckPass().execute( m )

    m = VarConstraintPass( dump=self.dump ).execute( m )
    m = ScheduleUpblkPass( dump=self.dump ).execute( m )
    m = GenerateTickPass( dump=self.dump ).execute( m )

    m = SignalCleanupPass().execute( m )
    
    return m