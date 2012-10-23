import syntax
import core_types 

from array_type import ArrayT, ScalarT
import closure_signatures 
from transform import Transform

from typed_syntax_helpers import const_tuple, const_int 

class LowerStructs(Transform):
  """
  The only non-scalar objects should all be created as explicit Structs
  """  
  def transform_Tuple(self, expr):
    struct_args = self.transform_expr_list(expr.elts)
    return syntax.Struct(struct_args, type = expr.type)
    
  def transform_Closure(self, expr):
    closure_args = self.transform_expr_list(expr.args)
    closure_id = closure_signatures.get_id(expr.type)
    closure_id_node = syntax.Const(closure_id, type = core_types.Int64)
    return syntax.Struct([closure_id_node] + closure_args, type = expr.type)
    
  def transform_Invoke(self, expr):
    new_closure = self.transform_expr(expr.closure)
    new_args =  self.transform_expr_list(expr.args) 
    return syntax.Invoke(new_closure, new_args, type = expr.type) 
    
  def transform_TupleProj(self, expr):
    new_tuple = self.transform_expr(expr.tuple)
    assert isinstance(expr.index, int)
    tuple_t = expr.tuple.type

    field_name, field_type  = tuple_t._fields_[expr.index]
    return syntax.Attribute(new_tuple, field_name, type = field_type)
    
  def transform_Array(self, expr):
    n = len(expr.elts)    
    array_t = expr.type
    assert isinstance(array_t, ArrayT)

    elt_t = array_t.elt_type
    assert isinstance(elt_t, ScalarT)
    ptr_t = core_types.ptr_type(elt_t)
    alloc = syntax.Alloc(elt_t, const_int(n), type = ptr_t)
    ptr_var = self.fresh_var(ptr_t, "data")
    self.insert_stmt(syntax.Assign(ptr_var, alloc))
    for (i, elt) in enumerate(self.transform_expr_list(expr.elts)):
      idx = syntax.Const(i, type = core_types.Int32)
      lhs = syntax.Index(ptr_var, idx)
      self.insert_stmt(syntax.Assign(lhs, elt))
    shape = const_tuple(n)
    strides = const_tuple(elt_t.nbytes)
    return syntax.Struct([ptr_var, shape, strides], type = expr.type)
  
def make_structs_explicit(fn):
  return LowerStructs(fn).apply()