from shapely.geometry import LineString, Point
import numpy as np
import itertools
import pandas as pd
import vispy.scene
import geopandas as gpd
from pandas import DataFrame
from multiprocesspandas import applyparallel

from src.main import (
    geometry_utilities,
    geometry_operations,
    mechanical_computations,
    classes,
    plotting,
)

# Main functions to compute the cable road which calls the other functions


def generate_possible_lines(
    road_points: list[Point],
    target_trees: gpd.GeoDataFrame,
    anchor_trees: gpd.GeoDataFrame,
    overall_trees: gpd.GeoDataFrame,
    slope_line: LineString,
    height_gdf: gpd.GeoDataFrame,
    plot_possible_lines: bool,
) -> tuple[DataFrame, dict]:
    """Compute which lines can be made from road_points to anchor_trees without having an angle greater than max_main_line_slope_deviation
    First, we generate all possible lines between  each point along the road and all head anchors.
    For those which do not deviate more than max_main_line_slope_deviation degrees from the slope line, we compute head anchor support trees along the lines.
    If those are present, we compute triples of tail anchor support trees.
    If those are present, valid configurations are appended to the respective lists.

    Args:
        road_points (_type_): _description_
        target_trees (_type_): _description_
        anchor_trees (_type_): _description_
        slope_line (_type_): _description_
        max_main_line_slope_deviation (_type_): How much the central of three lines can deviate from the slope
        max_anchor_distance (_type_): How far away should the anchors be at most

    Returns:
        _type_: _description_
    """
    max_main_line_slope_deviation = 45

    if plot_possible_lines:
        # Make a canvas and add simple view
        canvas = vispy.scene.SceneCanvas(keys="interactive", show=True)
        view = canvas.central_widget.add_view()
    else:
        view = None

    # generate the list of line candidates within max_slope_angle
    line_candidate_list = list(itertools.product(road_points, target_trees.geometry))
    line_candidate_list_combinations = [
        LineString(combination) for combination in line_candidate_list
    ]
    line_df = DataFrame(data={"line_candidates": line_candidate_list_combinations})
    print(len(line_df), " candidates initially")

    # filter by max_main_line_slope_deviation
    line_df["slope_deviation"] = [
        geometry_utilities.angle_between(line, slope_line)
        for line in line_candidate_list_combinations
    ]
    line_df = line_df[line_df["slope_deviation"] < max_main_line_slope_deviation]
    print(len(line_df), " after slope deviations")

    line_df = line_df.iloc[::10]

    # filter the candidates for support trees
    # overall_trees, target, point, possible_line
    line_df["tree_anchor_support_trees"] = [
        generate_tree_anchor_support_trees(
            overall_trees, Point(line.coords[1]), Point(line.coords[0]), line
        )
        for line in line_df["line_candidates"]
    ]
    # add to df and filter empty entries
    line_df = line_df[line_df["tree_anchor_support_trees"].apply(len) > 0]
    print(len(line_df), " after supports trees")

    # filter the triple angles for good supports
    line_df["possible_anchor_triples"], line_df["max_holding_force"] = zip(
        *[
            generate_triple_angle(Point(line.coords[0]), line, anchor_trees)
            for line in line_df["line_candidates"]
        ]
    )
    line_df = line_df[line_df["possible_anchor_triples"].notnull()]
    print(len(line_df), " after possible anchor triples")

    # check if we have no height obstructions - compute the supports we need according to line tension and anchor configs
    pos = []

    line_df["Cable Road Object"] = [
        compute_required_supports(
            line["possible_anchor_triples"],
            line["max_holding_force"],
            line["tree_anchor_support_trees"],
            height_gdf,
            overall_trees,
            from_line=line["line_candidates"],
        )
        for index, line in line_df.iterrows()
    ]

    # and filter lines out without successful lines
    line_df = line_df[line_df["Cable Road Object"].apply(lambda x: x is not False)]
    print(len(line_df), " after checking for height obstructions")

    if len(line_df) < 1:
        raise ValueError("No candidates left")

    # compute the angle between the line and the supports
    line_df["angle_between_supports"] = [
        mechanical_computations.compute_angle_between_supports(line, height_gdf)
        for line in line_df["line_candidates"]
    ]

    # create a dict of the coords of the starting points
    start_point_dict = dict(
        [(key, value.coords[0]) for key, value in enumerate(line_df["line_candidates"])]
    )

    if plot_possible_lines:
        plotting.plot_vispy_scene(height_gdf, view, pos)

    return line_df, start_point_dict


from itertools import pairwise


def decrement_tension_until_towers_anchors_supports_hold(
    tower_and_anchors_hold,
    supports_hold,
    this_cable_road,
    max_supported_forces,
    anchor_triplets,
    tree_anchor_support_trees,
    height_gdf,
):
    tower_and_anchors_hold = (
        mechanical_computations.check_if_tower_and_anchor_trees_hold(
            this_cable_road, max_supported_forces, anchor_triplets, height_gdf
        )
    )
    print("Tower and anchors hold:", tower_and_anchors_hold)
    if tower_and_anchors_hold:
        if this_cable_road.supported_segments:
            for current_segment, next_segment in pairwise(
                this_cable_road.supported_segments
            ):
                supports_hold = (
                    mechanical_computations.check_if_support_withstands_tension(
                        current_segment, next_segment
                    )
                )
        else:
            # set support holds to true since we dont have any
            supports_hold = True

    if tower_and_anchors_hold and supports_hold:
        return tower_and_anchors_hold, supports_hold
    else:
        this_cable_road.s_current_tension -= 10000
        return tower_and_anchors_hold, supports_hold


def compute_required_supports(
    anchor_triplets: list,
    max_supported_forces: list[float],
    tree_anchor_support_trees: list,
    height_gdf: gpd.GeoDataFrame,
    overall_trees: gpd.GeoDataFrame,
    pre_tension: int = 0,
    from_line: LineString = None,
    from_segment: classes.SupportedSegment = None,
) -> classes.Cable_Road:
    # sourcery skip: boolean-if-exp-identity, remove-unnecessary-cast
    """A function to check whether there are any points along the line candidate (spanned up by the starting/end points
     elevation plus the support height) which are less than min_height away from the line.

    Args:
        anchor_triplets (list): A list of anchor triplets, each triplet is a list of three points
        max_supported_forces (list[float]): A list of the maximum supported forces for each anchor triplet
        tree_anchor_support_trees (list): A list of the support trees for each anchor triplet
        height_gdf (gpd.GeoDataFrame): A GeoDataFrame containing the height information
        overall_trees (gpd.GeoDataFrame): A GeoDataFrame containing the trees
        pre_tension (int, optional): The pre-tension to start with. Defaults to 0.
        from_line (LineString, optional): The line to start with. Defaults to None, in which case from_segment must be given.
        from_segment (classes.SupportedSegment, optional): The segment to start with. Defaults to None.

    Returns:
        classes.Cable_Road: The cable road object

    """
    # TODO - set this up with check if  this is the first iteration, so we can properly set up the tower
    if from_line:
        this_cable_road = classes.initialize_cable_road_with_supports(
            from_line, height_gdf, pre_tension=pre_tension, is_tower=True
        )
    elif from_segment:
        this_cable_road = from_segment.cable_road
    else:
        raise ValueError("No line or segment given")

    print("Tension to begin with is", this_cable_road.s_current_tension)

    # set supports_Hold to false if we have supports and need to check them, else set to true
    tower_and_anchors_hold = False
    supports_hold = False if this_cable_road.count_segments() > 0 else True

    # decrement by 10kn increments while checking if the towers and intermediate supports hold
    # if we found a tension that is high enough and anchors support it, we continue
    min_cr_tension = 30000
    while not (tower_and_anchors_hold and supports_hold):
        (
            tower_and_anchors_hold,
            supports_hold,
        ) = decrement_tension_until_towers_anchors_supports_hold(
            tower_and_anchors_hold,
            supports_hold,
            this_cable_road,
            max_supported_forces,
            anchor_triplets,
            tree_anchor_support_trees,
            height_gdf,
        )

        if this_cable_road.s_current_tension < min_cr_tension:
            print("CR tension is too low with ", this_cable_road.s_current_tension)
            return return_failed()

    this_cable_road.anchors_hold = True

    print("After the iterative process it is now", this_cable_road.s_current_tension)

    # check if it feasible to continue or to return if we already have a successful line
    return_early = evaluate_cr_collisions(this_cable_road)
    if return_early:
        # TODO - fix this, provide with sensible args
        return return_sucessful(this_cable_road)

    # enter the next recursive loop if not b creating supports
    else:
        print("Need to find supports")
        # get the distance candidates
        distance_candidates = setup_support_candidates(this_cable_road, overall_trees)

        if distance_candidates.empty:
            return return_failed()

        # loop through the candidates to check if one combination has no obstructions
        for candidate_index in distance_candidates.index:
            # create the prospective segment
            (
                left_segment,
                right_segment,
                candidate_tree,
            ) = create_left_right_segments_and_support_tree(
                overall_trees,
                this_cable_road,
                candidate_index,
                height_gdf,
            )

            segments_feasible = check_segment_for_feasibility(
                left_segment,
                right_segment,
                candidate_tree,
                this_cable_road,
            )
            if segments_feasible:
                # TODO - fix this return
                return return_sucessful(this_cable_road)

        # if we passed through the loop without finding suitable candidates, set the first candidate as support and find sub-supports recursively
        print(
            "didnt find suitable candidate - select first candidate as support and iterate"
        )

        # set the first candidate as support and add it to the current CR
        this_cable_road = set_up_recursive_supports(
            this_cable_road,
            overall_trees,
            distance_candidates,
            height_gdf,
        )

        # test for collisions left and right - enter the recursive loop to compute subsupports
        left_CR = compute_required_supports(
            anchor_triplets,
            max_supported_forces,
            tree_anchor_support_trees,
            height_gdf,
            overall_trees,
            pre_tension=this_cable_road.s_current_tension,
            from_line=None,
            from_segment=left_segment,
        )

        right_CR = compute_required_supports(
            anchor_triplets,
            max_supported_forces,
            tree_anchor_support_trees,
            height_gdf,
            overall_trees,
            pre_tension=this_cable_road.s_current_tension,
            from_line=None,
            from_segment=right_segment,
        )

        # update the current CR with the sub CRs and count the supports
        this_cable_road.number_sub_segments = this_cable_road.count_segments(
            this_cable_road.number_sub_segments
        )

        # computed sub-supports and see if we had enough
        if this_cable_road.number_sub_segments > 4:
            print("too many supports, returning failed")
            return return_failed()
        else:
            print("found enough supports and returning successful cable road")
            return return_sucessful(this_cable_road)


# helper function to have return functions in one place
def return_failed() -> bool:
    return False


# TODO - how to collect the supports and locations for all sub crs?
def return_sucessful(
    this_cable_road: classes.Cable_Road,
) -> classes.Cable_Road:
    return this_cable_road


def current_tension(this_cable_road: classes.Cable_Road) -> int:
    """return the current tension of the cable road"""
    return int(this_cable_road.s_current_tension)


def raise_height_and_check_tension(
    left_segment: classes.SupportedSegment,
    right_segment: classes.SupportedSegment,
    height_index: int,
) -> bool:
    """raise the height of the support and check if it now withstands tension"""
    print("raising height to ", height_index)
    # increase the support height
    # TODO - does this propagate, ie. is the underlying cable road updated? probably not. need to check
    left_segment.right_support.attachment_height = height_index
    right_segment.left_support.attachment_height = height_index

    return check_support_tension_and_collision(left_segment, right_segment)


def set_up_recursive_supports(
    this_cable_road: classes.Cable_Road,
    overall_trees: gpd.GeoDataFrame,
    distance_candidates: gpd.GeoDataFrame,
    height_gdf: gpd.GeoDataFrame,
) -> classes.Cable_Road:
    """set up the next iteration for finding supports.
    We select the first candidate as support, create a segment for it and add it to the cable road.

    Args:
        this_cable_road (classes.Cable_Road): the current cable road
        overall_trees (gpd.GeoDataFrame): the overall trees
        distance_candidates (gpd.GeoDataFrame): the candidates for the support, sorted by distance
        height_gdf (gpd.GeoDataFrame): the height gdf

    Returns:
        classes.Cable_Road: the updated cable road

    """

    # proceed with the working cr and find sub-supports - fetch the candidate we last looked at
    # select first support as starting point - this is the most protruding point
    candidate = distance_candidates.index[0]

    # set up the sideways cable roads and support segment
    (
        left_segment,
        right_segment,
        support_tree,
    ) = create_left_right_segments_and_support_tree(
        overall_trees, this_cable_road, candidate, height_gdf
    )
    this_cable_road.supported_segments.extend((left_segment, right_segment))

    return this_cable_road


def check_segment_for_feasibility(
    left_segment: classes.SupportedSegment,
    right_segment: classes.SupportedSegment,
    candidate_tree: gpd.GeoSeries,
    this_cable_road: classes.Cable_Road,
):
    # check if the candidate is too close to the anchor
    if candidate_is_too_close_to_anchor(
        left_segment
    ) or candidate_is_too_close_to_anchor(right_segment):
        return False

    # iterate through the possible attachments of the support and see if we touch ground
    # start with at least three meters height
    support_withstands_tension = False
    min_height = 6
    if len(candidate_tree.height_series) < min_height:
        return False

    for height_index in range(min_height, len(candidate_tree.height_series)):
        support_withstands_tension = raise_height_and_check_tension(
            left_segment, right_segment, height_index
        )

        # if the support doesnt withstand tension, we continue to the next height
        if not support_withstands_tension:
            if left_segment.right_support.attachment_height > 20:
                print("Attachment raised too high, segment not feasible")
                return False
            print("iterating through height series since support doesnt hold")
            continue

        if (
            left_segment.cable_road.no_collisions
            and right_segment.cable_road.no_collisions
        ):
            # we found a viable configuration - break out of this loop
            break
        print("iterating through height series since we have collisions")

    # no collisions were found and support holds, return our current supports
    if (
        left_segment.cable_road.no_collisions
        and right_segment.cable_road.no_collisions
        and support_withstands_tension
    ):
        print("found viable sub-config")
        this_cable_road.supported_segments.extend((left_segment, right_segment))
        return return_sucessful(this_cable_road)
    return False


def evaluate_cr_collisions(
    this_cable_road: classes.Cable_Road,
):
    """evaluate the anchors and collisions of the cable road"""
    mechanical_computations.check_if_no_collisions_cable_road(this_cable_road)

    if this_cable_road.count_segments() and this_cable_road.count_segments() < 4:
        print("more than 4 supports not possible")
        return return_failed()

    if this_cable_road.no_collisions:
        print("Found no collisions")
        return return_sucessful(this_cable_road)
    else:
        print("Found collisions")
        return return_failed()


def candidate_is_too_close_to_anchor(support_segment) -> bool:
    if len(support_segment.cable_road.points_along_line) < 4:
        print(
            "candidate too close to anchor, skipping in check if no coll overall line sideways CR"
        )
        return True
    return False


def check_support_tension_and_collision(
    left_segment: classes.SupportedSegment,
    right_segment: classes.SupportedSegment,
) -> bool:
    """check if the support withstands tension and if there are collisions.
    Return false if support doesnt hold or if there are collisions
    Args:
        left_segment (classes.SupportedSegment): The left segment
        right_segment (classes.SupportedSegment): The right segment
        height_index (int): The height index of the support

    Returns:
        bool: True if support holds and there are no collisions
    """
    support_withstands_tension = (
        mechanical_computations.check_if_support_withstands_tension(
            left_segment, right_segment
        )
    )

    if (not support_withstands_tension) or (
        left_segment.right_support.attachment_height
    ) > 20:
        # next candidate - tension just gets worse with more height
        return False

    # 6. no collisions left and right? go to next candidate if this one is already not working out
    # first set the height of the support
    mechanical_computations.check_if_no_collisions_cable_road(left_segment.cable_road)
    mechanical_computations.check_if_no_collisions_cable_road(right_segment.cable_road)

    if left_segment.cable_road.no_collisions and right_segment.cable_road.no_collisions:
        return True
    return False


def generate_triple_angle(
    point: Point, line_candidate: LineString, anchor_trees: gpd.GeoDataFrame
) -> tuple[list, list] | tuple[None, None]:
    """Generate a list of line-triples that are within correct angles to the road point
    and slope line and the corresponding max supported force by the center tree.

    Checks whether:
    - anchor trees are within (less than) correct distance
    - all of those lines have a deviation < max outer anchor angle to the slope line
    - outward anchor trees are within 20 to 60m to each other



    Args:
        point (_type_): The road point we want to check for possible anchors
        line_candidate (_type_): _description_
        anchor_trees (_type_): _description_
        max_anchor_distance (_type_): _description_
        max_outer_anchor_angle (_type_): Max angle between right and left line
        min_outer_anchor_angle (_type_): Minimum angle between right and left line
        max_center_tree_slope_angle (_type_): Max deviation of center line from slope line

    Returns:
        list: A list of possible triple angles for this cable road in the form of [(center line, left line, right line), ...]
        list: A list of max supported force of the corresponding center tree
    """
    min_outer_anchor_angle = 20
    max_outer_anchor_angle = 50
    max_center_tree_slope_angle = 5
    max_anchor_distance = 40
    min_anchor_distane = 15

    # 1. get list of possible anchors -> anchor trees
    anchor_trees_working_copy = anchor_trees.copy()

    # 2. check which points are within distance
    anchor_trees_working_copy = anchor_trees_working_copy[
        (anchor_trees_working_copy.geometry.distance(point) <= max_anchor_distance)
        & (anchor_trees_working_copy.geometry.distance(point) >= min_anchor_distane)
    ]

    if anchor_trees_working_copy.empty or len(anchor_trees_working_copy) < 3:
        return None, None

    # 3. create lines to all these possible connections
    anchor_trees_working_copy["anchor_line"] = anchor_trees_working_copy.geometry.apply(
        lambda x: LineString([x, point])
    )

    # compute the angle between the slope line and the anchor line and get two dfs with possible center and side trees
    anchor_trees_working_copy["slope_angle"] = anchor_trees_working_copy[
        "anchor_line"
    ].apply(lambda x: geometry_utilities.angle_between(x, line_candidate))

    central_trees = anchor_trees_working_copy[
        anchor_trees_working_copy["slope_angle"].between(0, max_center_tree_slope_angle)
    ].copy()
    side_trees = anchor_trees_working_copy[
        anchor_trees_working_copy["slope_angle"].between(
            min_outer_anchor_angle, max_outer_anchor_angle
        )
    ]

    if len(central_trees) < 3 or len(side_trees) < 2:
        return None, None

    central_trees.loc[:, "possible_anchor_triples"] = central_trees[
        "anchor_line"
    ].apply(
        lambda x: [
            (x, LineString([y, point]), LineString([z, point]))
            for y, z in itertools.combinations(side_trees.geometry, 2)
            if y.distance(z) > 20 and y.distance(z) < 60
        ]
    )

    # if this did not yield viable anchors, proceed
    if len(central_trees["possible_anchor_triples"].sum()) < 1:
        return None, None
    else:
        return (
            # return the first combination per main anchor line
            [sublist[0] for sublist in central_trees["possible_anchor_triples"]],
            central_trees["max_holding_force"].to_list(),
        )


def generate_tree_anchor_support_trees(
    overall_trees: gpd.GeoDataFrame,
    target: Point,
    point: Point,
    possible_line: LineString,
):
    """find trees in overall_trees along the last bit of the possible_line that are close to the line and can serve as support tree

    Args:
        overall_trees (_type_): GDF of all trees
        target (_type_): The last tree
        point (_type_): The road point we are starting from
        possible_line (_type_): The limne between target and point

    Returns:
        _type_: _description_
    """
    # Parameters
    min_support_sideways_distance = 0.1
    max_support_sideways_distance = 1.5
    min_support_anchor_distance = 10
    max_support_anchor_distance = 20

    # find those trees that are within the sideways distance to the proposed line
    support_tree_candidates = overall_trees[
        overall_trees.geometry.distance(possible_line).between(
            min_support_sideways_distance, max_support_sideways_distance
        )
    ]

    # find those trees that are within the right distance to the target tree
    support_tree_candidates = support_tree_candidates[
        support_tree_candidates.geometry.distance(target).between(
            min_support_anchor_distance, max_support_anchor_distance
        )
    ]

    # select only those support tree candidates which are close to the roadside point than the target tree
    support_tree_candidates = support_tree_candidates[
        support_tree_candidates.geometry.distance(point) < target.distance(point)
    ]

    return support_tree_candidates


def create_candidate_points_and_lines(
    candidate_index: int,
    start_point: Point,
    end_point: Point,
    candidate_point: Point,
    overall_trees,
) -> tuple[Point, LineString, LineString]:
    # 5. create the new candidate point and lines to/from it
    road_to_support_line = LineString([start_point, candidate_point])
    support_to_anchor_line = LineString([candidate_point, end_point])
    # get the location of the support point - why am Ii not able to do this with intermediate_support_candidates?
    new_support_point = overall_trees.iloc[candidate_index].geometry
    road_to_support_line = LineString([start_point, new_support_point])
    support_to_anchor_line = LineString([new_support_point, end_point])

    return new_support_point, road_to_support_line, support_to_anchor_line


def setup_support_candidates(
    this_cable_road: classes.Cable_Road,
    overall_trees: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Generate a list of support candidates for the current cable road.

    Args:
        this_cable_road (classes.Cable_Road): The cable road for which we want to find support candidates
        overall_trees (gpd.GeoDataFrame): The overall trees

    Returns:
        gpd.GeoDataFrame: The candidate trees for the support
    """
    print("Setting up support candidates")

    # 1. get the point of contact
    lowest_point_height = min(this_cable_road.sloped_line_to_floor_distances)
    sloped_line_to_floor_distances_index = int(
        np.where(this_cable_road.sloped_line_to_floor_distances == lowest_point_height)[
            0
        ]
    )

    # 2. Get all trees which are within 0.5-2 meter distance to the line in general
    intermediate_support_candidates = overall_trees[
        (overall_trees.distance(this_cable_road.line) < 2)
        & (overall_trees.distance(this_cable_road.line) > 0.5)
    ]

    # 3. stop if there are no candidates, also stop if we have more than four supports - not viable
    if len(intermediate_support_candidates) < 1 or this_cable_road.count_segments() > 3:
        return gpd.GeoDataFrame()

    # 4. enumerate through list of candidates - sort by distance to the point of contact
    point_of_contact = this_cable_road.points_along_line[
        sloped_line_to_floor_distances_index
    ]

    distance_candidates = intermediate_support_candidates.distance(point_of_contact)
    distance_candidates = distance_candidates.sort_values(ascending=True)

    return distance_candidates


def create_left_right_segments_and_support_tree(
    overall_trees, this_cable_road, candidate_index, height_gdf
) -> tuple[classes.SupportedSegment, classes.SupportedSegment, gpd.GeoSeries]:
    """Create the sideways cable roads as well as the candidate tree and return them

    Args:
        overall_trees (_type_): _description_
        this_cable_road (_type_): _description_
        candidate (_type_): _description_
        height_gdf (_type_): _description_

    Returns:
        support_segment: The left support segment
        support_segment: The right support segment
        gpd.GeoSeries: The row of the candidate tree
    """

    # need to add the height and force per tree here
    candidate_tree = overall_trees.iloc[candidate_index]

    # create lines and points left and right
    (
        new_support_point,
        road_to_support_line,
        support_to_anchor_line,
    ) = create_candidate_points_and_lines(
        candidate_index,
        this_cable_road.start_point,
        this_cable_road.end_point,
        candidate_tree.geometry,
        overall_trees,
    )

    # Create the supports for the left CR segment
    road_to_support_cable_road_left_support = this_cable_road.left_support
    road_to_support_cable_road_right_support = classes.Support(
        attachment_height=6,
        xy_location=Point(road_to_support_line.coords[1]),
        height_gdf=height_gdf,
        max_supported_force=candidate_tree.max_supported_force_series,
    )

    # Create the supports for the right CR segment
    support_to_anchor_cable_road_left_support = road_to_support_cable_road_right_support
    support_to_anchor_cable_road_right_support = this_cable_road.right_support

    # create left and right sub_cableroad
    road_to_support_cable_road = classes.Cable_Road(
        road_to_support_line,
        height_gdf,
        road_to_support_cable_road_left_support,
        road_to_support_cable_road_right_support,
        pre_tension=this_cable_road.s_current_tension,
    )
    support_to_anchor_cable_road = classes.Cable_Road(
        support_to_anchor_line,
        height_gdf,
        support_to_anchor_cable_road_left_support,
        support_to_anchor_cable_road_right_support,
        pre_tension=this_cable_road.s_current_tension,
    )

    # and both segments, which in turn contain the corresponding CRs and the supports
    left_segment = classes.SupportedSegment(
        road_to_support_cable_road,
        this_cable_road.left_support,
        road_to_support_cable_road_right_support,
    )

    right_segment = classes.SupportedSegment(
        support_to_anchor_cable_road,
        support_to_anchor_cable_road_left_support,
        this_cable_road.right_support,
    )

    return left_segment, right_segment, candidate_tree