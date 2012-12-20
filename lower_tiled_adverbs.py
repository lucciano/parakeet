import adverb_helpers
import array_type
import copy
import syntax
import syntax_helpers
import tuple_type

from core_types import Int32, Int64
from lower_adverbs import LowerAdverbs
from transform import Transform

int64_array_t = array_type.make_array_type(Int64, 1)

class LowerTiledAdverbs(Transform):
  def __init__(self, fn, nesting_idx=-1, tile_param_array=None):
    Transform.__init__(self, fn)
    self.num_tiled_adverbs = 0
    self.nesting_idx = nesting_idx
    self.tiling = False
    if tile_param_array == None:
      self.tile_param_array = self.fresh_var(int64_array_t, "tile_params")
    else:
      self.tile_param_array = tile_param_array

  def transform_TypedFn(self, expr):
    nested_lower = LowerTiledAdverbs(expr, nesting_idx=self.nesting_idx,
                                     tile_param_array=self.tile_param_array)
    return nested_lower.apply()

  def transform_Map(self, expr):
    #self.tiling = False
    return expr

  def transform_Reduce(self, expr):
    #self.tiling = False
    return expr

  def transform_Scan(self, expr):
    #self.tiling = False
    return expr

  def transform_TiledMap(self, expr):
    self.tiling = True
    self.nesting_idx += 1
    args = expr.args
    axis = syntax_helpers.unwrap_constant(expr.axis)

    # TODO: Should make sure that all the shapes conform here,
    # but we don't yet have anything like assertions or error handling
    max_arg = adverb_helpers.max_rank_arg(args)
    niters = self.shape(max_arg, axis)

    # Create the tile size variable and find the number of tiles
    tile_size = self.index(self.tile_param_array, self.nesting_idx)
    self.num_tiled_adverbs += 1
    num_tiles = self.div(niters, tile_size, name="num_tiles")
    loop_bound = self.mul(num_tiles, tile_size, "loop_bound")
    callable_fn = self.transform_expr(expr.fn)
    inner_fn = self.get_fn(callable_fn)
    nested_has_tiles = inner_fn.arg_names[-1] == self.tile_param_array.name

    elt_t = expr.type.elt_type
    slice_t = array_type.make_slice_type(Int64, Int64, Int64)

    # Hackishly execute the first tile to get the output shape
    init_slice_bound = self.fresh_i64("init_slice_bound")
    init_slice_cond = self.lt(tile_size, niters, "init_slice_cond")
    full_slice_body = [syntax.Assign(init_slice_bound, tile_size)]
    part_slice_body = [syntax.Assign(init_slice_bound, niters)]
    init_merge = {init_slice_bound.name:(tile_size, niters)}
    self.blocks += syntax.If(init_slice_cond, full_slice_body, part_slice_body,
                             init_merge)
    init_slice = syntax.Slice(syntax_helpers.zero_i64, init_slice_bound,
                              syntax_helpers.one_i64, slice_t)
    init_slice_args = [self.index_along_axis(arg, axis, init_slice)
                       for arg in args]
    if nested_has_tiles:
      init_slice_args.append(self.tile_param_array)
    rslt_init = self.assign_temp(syntax.Call(callable_fn, init_slice_args,
                                             type=inner_fn.return_type),
                                 "rslt_init")

    init_shape = self.shape(rslt_init)
    other_shape_els = [self.tuple_proj(init_shape, i)
                       for i in range(1, len(init_shape.type.elt_types))]
    out_shape = self.tuple([niters] + other_shape_els)
    array_result = self.alloc_array(elt_t, out_shape, "array_result")
    init_output_idxs = self.index_along_axis(array_result, 0, init_slice)
    self.assign(init_output_idxs, rslt_init)

    i, i_after, merge = self.loop_counter("i", tile_size)
    cond = self.lt(i, loop_bound)

    self.blocks.push()
    # Take care of stragglers via checking bound every iteration.
    next_bound = self.add(i, tile_size, "next_bound")
    tile_cond = self.lte(next_bound, niters)
    tile_merge = {i_after.name:(next_bound, niters)}
    full_slice_body = [syntax.Assign(i_after, next_bound)]
    part_slice_body = [syntax.Assign(i_after, niters)]
    self.blocks += syntax.If(tile_cond, full_slice_body, part_slice_body,
                             tile_merge)

    tile_bounds = syntax.Slice(i, i_after, syntax_helpers.one(Int64),
                               type=slice_t)
    nested_args = [self.index_along_axis(arg, axis, tile_bounds)
                   for arg in args]
    # Output always to the 0 axis, as per discussion.
    output_idxs = self.index_along_axis(array_result, 0, tile_bounds)

    if nested_has_tiles:
      nested_args.append(self.tile_param_array)
    nested_call = syntax.Call(callable_fn, nested_args,
                              type=inner_fn.return_type)
    self.assign(output_idxs, nested_call)
    body = self.blocks.pop()

    self.blocks += syntax.While(cond, body, merge)

    return array_result

  def transform_TiledReduce(self, expr):
    self.tiling = True
    def alloc_maybe_array(isarray, t, shape, name=None):
      if isarray:
        return self.alloc_array(t, shape, name)
      else:
        return self.fresh_var(t, name)

    self.nesting_idx += 1
    args = expr.args
    axis = syntax_helpers.unwrap_constant(expr.axis)

    # TODO: Should make sure that all the shapes conform here,
    # but we don't yet have anything like assertions or error handling
    max_arg = adverb_helpers.max_rank_arg(args)
    niters = self.shape(max_arg, axis)

    # Create the tile size variable and find the number of tiles
    tile_size = self.index(self.tile_param_array, self.nesting_idx)
    self.num_tiled_adverbs += 1

    slice_t = array_type.make_slice_type(Int64, Int64, Int64)
    callable_fn = self.transform_expr(expr.fn)
    inner_fn = self.get_fn(callable_fn)
    callable_combine = self.transform_expr(expr.combine)
    inner_combine = self.get_fn(callable_combine)
    isarray = isinstance(expr.type, array_type.ArrayT)
    nested_has_tiles = inner_fn.arg_names[-1] == self.tile_param_array.name
    elt_t = array_type.elt_type(expr.type)

    # Hackishly execute the first tile to get the output shape
    init_slice_bound = self.fresh_i64("init_slice_bound")
    init_slice_cond = self.lt(tile_size, niters, "init_slice_cond")
    full_slice_body = [syntax.Assign(init_slice_bound, tile_size)]
    part_slice_body = [syntax.Assign(init_slice_bound, niters)]
    init_merge = {init_slice_bound.name:(tile_size, niters)}
    self.blocks += syntax.If(init_slice_cond, full_slice_body, part_slice_body,
                             init_merge)
    init_slice = syntax.Slice(syntax_helpers.zero_i64, init_slice_bound,
                              syntax_helpers.one_i64, slice_t)
    init_slice_args = [self.index_along_axis(arg, axis, init_slice)
                       for arg in args]
    if nested_has_tiles:
      init_slice_args.append(self.tile_param_array)
    rslt_init = self.assign_temp(syntax.Call(callable_fn, init_slice_args,
                                             type=callable_fn.return_type),
                                 "rslt_init")
    out_shape = self.shape(rslt_init)
    rslt_t = rslt_init.type

    # Lift the initial value and fill it
    init = alloc_maybe_array(isarray, elt_t, out_shape, "init")
    def init_unpack(i, cur):
      if i == 0:
        return syntax.Assign(cur, expr.init)
      else:
        self.blocks.push()
        j, j_after, merge = self.loop_counter("j")
        init_cond = self.lt(j, self.shape(cur, 0))
        n = self.index_along_axis(cur, 0, j)
        self.blocks += init_unpack(i-1, n)
        self.assign(j_after, self.add(j, syntax_helpers.one_i64))
        body = self.blocks.pop()
        return syntax.While(init_cond, body, merge)
    num_exps = array_type.get_rank(init.type) - \
               array_type.get_rank(expr.init.type)
    self.blocks += init_unpack(num_exps, init)

    loop_rslt = self.fresh_var(rslt_t, "loop_rslt")
    rslt_tmp = self.fresh_var(rslt_t, "rslt_tmp")
    rslt_after = self.fresh_var(rslt_t, "rslt_after")
    i, i_after, merge = self.loop_counter("i", init_slice_bound)
    loop_cond = self.lt(i, niters)
    merge[loop_rslt.name] = (init, rslt_after)

    self.blocks.push()
    # Take care of stragglers via checking bound every iteration.
    next_bound = self.add(i, tile_size, "next_bound")
    tile_cond = self.lte(next_bound, niters)
    tile_merge = {i_after.name:(next_bound, niters)}
    full_slice_body = [syntax.Assign(i_after, next_bound)]
    part_slice_body = [syntax.Assign(i_after, niters)]
    self.blocks += syntax.If(tile_cond, full_slice_body, part_slice_body,
                             tile_merge)

    tile_bounds = syntax.Slice(i, i_after, syntax_helpers.one(Int64),
                               type=slice_t)
    nested_args = [self.index_along_axis(arg, axis, tile_bounds)
                   for arg in args]
    if nested_has_tiles:
      nested_args.append(self.tile_param_array)
    nested_call = syntax.Call(callable_fn, nested_args,
                              type=inner_fn.return_type)
    self.assign(rslt_tmp, nested_call)
    nested_combine = syntax.Call(callable_combine,
                                 [loop_rslt, rslt_tmp],
                                 type=inner_combine.return_type)
    self.assign(rslt_after, nested_combine)
    loop_body = self.blocks.pop()
    self.blocks += syntax.While(loop_cond, loop_body, merge)

    return loop_rslt

  def post_apply(self, fn):
    if self.tiling:
      fn.arg_names.append(self.tile_param_array.name)
      fn.input_types += (int64_array_t,)
      fn.type_env[self.tile_param_array.name] = int64_array_t
    return fn
