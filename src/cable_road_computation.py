from shapely.geometry import LineString, Point
import numpy as np
import itertools
import pandas as pd
import vispy.scene
import geopandas as gpd
from pandas import DataFrame

from src import (
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

    line_df = line_df.iloc[::50]

    # filter the candidates for support trees
    # overall_trees, target, point, possible_line
    line_df["possible_support_trees"] = [
        generate_support_trees(
            overall_trees, Point(line.coords[1]), Point(line.coords[0]), line
        )
        for line in line_df["line_candidates"]
    ]
    # add to df and filter empty entries
    line_df = line_df[line_df["possible_support_trees"].apply(len) > 0]
    print(len(line_df), " after supports trees")

    # filter the triple angles for good supports
    line_df["possible_anchor_triples"], line_df["max_supported_force"] = zip(
        *[
            generate_triple_angle(Point(line.coords[0]), line, anchor_trees)
            for line in line_df["line_candidates"]
        ]
    )
    line_df = line_df[line_df["possible_anchor_triples"].notnull()]
    print(len(line_df), " after possible anchor triples")

    # check if we have no height obstructions - compute the supports we need according to line tension and anchor configs
    pos = []
    line_df["number_of_supports"], line_df["location_of_int_supports"] = zip(
        *[
            compute_required_supports(
                line["line_candidates"],
                line["possible_anchor_triples"],
                line["max_supported_force"],
                height_gdf,
                0,
                plot_possible_lines,
                view,
                [],
                overall_trees,
                pos,
            )
            for index, line in line_df.iterrows()
        ]
    )
    # and filter lines out without successful lines
    line_df = line_df[line_df["number_of_supports"].apply(lambda x: x is not False)]
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


# Overarching functions to compute the cable road
def compute_initial_cable_road(
    possible_line: LineString,
    height_gdf: gpd.GeoDataFrame,
    initial_tension=None,
) -> classes.Cable_Road:
    """Create a CR object and compute its initial properties like height, points along line etc

    Args:
        possible_line (_type_): _description_
        height_gdf (_type_): _description_

    Returns:
        _type_: Cable_Road Objects
    """
    this_cable_road = classes.Cable_Road(possible_line)

    this_cable_road.start_point_height = (
        geometry_operations.fetch_point_elevation(
            this_cable_road.start_point, height_gdf, this_cable_road.max_deviation
        )
        + this_cable_road.support_height
    )
    this_cable_road.end_point_height = (
        geometry_operations.fetch_point_elevation(
            this_cable_road.end_point, height_gdf, this_cable_road.max_deviation
        )
        + this_cable_road.support_height
    )

    # fetch the floor points along the line
    this_cable_road.points_along_line = geometry_operations.generate_road_points(
        this_cable_road.line, interval=2
    )

    # get the height of those points and set them as attributes to the CR object
    this_cable_road.compute_line_height(height_gdf)

    # generate floor points and their distances
    this_cable_road.floor_points = list(
        zip(
            [point.x for point in this_cable_road.points_along_line],
            [point.y for point in this_cable_road.points_along_line],
            this_cable_road.floor_height_below_line_points,
        )
    )

    this_cable_road.line_to_floor_distances = np.asarray(
        [
            geometry_utilities.lineseg_dist(
                point,
                this_cable_road.line_start_point_array,
                this_cable_road.line_end_point_array,
            )
            for point in this_cable_road.floor_points
        ]
    )

    # get the rope length
    this_cable_road.b_length_whole_section = this_cable_road.start_point.distance(
        this_cable_road.end_point
    )

    this_cable_road.c_rope_length = geometry_utilities.distance_between_3d_points(
        this_cable_road.line_start_point_array, this_cable_road.line_end_point_array
    )

    if initial_tension:
        this_cable_road.s_current_tension = initial_tension

    return this_cable_road


def compute_required_supports(
    possible_line: LineString,
    anchor_triplets: list,
    max_supported_forces: list[float],
    height_gdf: gpd.GeoDataFrame,
    current_supports: int,
    plot_possible_lines: bool,
    view: vispy.scene.ViewBox,
    location_supports: list,
    overall_trees: gpd.GeoDataFrame,
    pos: list,
    pre_tension=None,
):
    """A function to check whether there are any points along the line candidate (spanned up by the starting/end points
     elevation plus the support height) which are less than min_height away from the line.

    Args:
        possible_line (_type_): _description_
        height_gdf (_type_): _description_

    Returns:
        _type_: _description_
    """
    # print("compute_required_supports")
    this_cable_road = compute_initial_cable_road(possible_line, height_gdf)

    if not pre_tension:
        mechanical_computations.initialize_line_tension(
            this_cable_road, current_supports
        )
    else:
        this_cable_road.s_current_tension = pre_tension

    print(this_cable_road.s_current_tension)

    # tension the line and check if anchors hold and we have collisions
    mechanical_computations.check_if_no_collisions_overall_line(
        this_cable_road,
        plot_possible_lines,
        view,
        pos,
        current_supports,
        anchor_triplets,
        max_supported_forces,
        pre_tension,
        height_gdf,
    )

    if this_cable_road.no_collisions and this_cable_road.anchors_hold:
        print(current_supports)
        print("successful on first try")
        return current_supports, location_supports

    # exit this line since anchors dont hold and supports wont help with that
    if not this_cable_road.anchors_hold:
        print("anchors dont hold")
        return False, False

    if current_supports and current_supports < 4:
        print("more than 4 supports not possible")
        return False, False
    # enter the next recursive loop if not b creating supports
    else:
        # get the distance candidates
        distance_candidates = setup_support_candidates(
            this_cable_road, overall_trees, current_supports, possible_line
        )

        if distance_candidates is False:
            return False, False

        # loop through the candidates to check if one has no obstructions
        for candidate in distance_candidates.index:
            # print("looping through distance candidates")
            # fetch the candidate

            (
                road_to_support_cable_road,
                support_to_anchor_cable_road,
                candidate_tree,
            ) = create_sideways_cableroads(
                overall_trees, this_cable_road, candidate, height_gdf
            )
            # do I also need to re-init max tension?

            # iterate through the possible attachments of the support and see if we touch ground
            for diameters_index in range(len(candidate_tree.height_series)):
                # skip if we are at height 0
                if candidate_tree.height_series[diameters_index] == 0:
                    continue
                support_withstands_tension = (
                    mechanical_computations.check_if_support_withstands_tension(
                        candidate_tree.diameter_series[diameters_index],
                        candidate_tree.height_series[diameters_index],
                        road_to_support_cable_road,
                        support_to_anchor_cable_road,
                        this_cable_road.s_current_tension,
                    )
                )
                if not support_withstands_tension:
                    # next candidate - tension just gets worse with more height
                    break

                # 6. no collisions left and right? go to next candidate if this one is already not working out
                mechanical_computations.check_if_no_collisions_segments(
                    road_to_support_cable_road
                )
                if not road_to_support_cable_road.no_collisions:
                    continue

                mechanical_computations.check_if_no_collisions_segments(
                    support_to_anchor_cable_road
                )

                if (
                    support_to_anchor_cable_road.no_collisions
                    and road_to_support_cable_road.no_collisions
                ):
                    # we found a viable configuration - break out of this loop and print if desired
                    break

            # no collisions were found and support holds, return our current supports
            if (
                road_to_support_cable_road.no_collisions
                and support_to_anchor_cable_road.no_collisions
                and support_withstands_tension
            ):
                print("found viable sub-config")
                current_supports += 1
                location_supports.append(candidate_tree.geometry)
                if plot_possible_lines:
                    plotting.plot_lines(road_to_support_cable_road, pos)
                    plotting.plot_lines(support_to_anchor_cable_road, pos)
                return current_supports, location_supports

        # if we passed through the loop without finding suitable candidates, set the first candidate as support and find sub-supports recursively
        print("didnt find suitable candidate")

        # proceed with the working cr and find sub-supports - fetch the candidate we last looked at
        # increment the supports correspondingly
        current_supports += 1
        first_candidate_point = overall_trees.iloc[
            distance_candidates.index[0]
        ].geometry
        location_supports.append(first_candidate_point)

        # select first support as starting point
        candidate = distance_candidates.index[0]
        (
            road_to_support_cable_road,
            support_to_anchor_cable_road,
            candidate_tree,
        ) = create_sideways_cableroads(
            overall_trees, this_cable_road, candidate, height_gdf
        )

        # test for collisions left and right - enter the recursive loop to compute subsupports
        current_supports, location_supports = test_collisions_left_right(
            [road_to_support_cable_road, support_to_anchor_cable_road],
            current_supports,
            location_supports,
            overall_trees,
            pos,
            plot_possible_lines,
            view,
            anchor_triplets,
            max_supported_forces,
            height_gdf,
        )

        # computed sub-supports and see if we had enough
        if current_supports and current_supports > 4 or current_supports is False:
            return False, False
        else:
            return current_supports, location_supports


# Generating different structures


def generate_triple_angle(
    point: Point, line_candidate: LineString, anchor_trees: gpd.GeoDataFrame
) -> tuple[list, list] | tuple[None, None]:
    """Generate a list of line-triples that are within correct angles to the road point and slope line.
    Checks whether:
    - anchor trees are within (less than) correct distance
    - all of those lines have a deviation < max outer anchor angle to the slope line
    Then we create pairs of lines within max_outer_anchor_angle to each other AND one of them with <max_center_tree_slope_angle to the slope line
    This results in pairs of lines were one is the inner and one the right-most (or vice versa)
    Then, we check for a third possible line if it is within 2*min_outer_anch_angle and 2* max_anchor_angle for one point and within min_outer, max_outer for the other -> so two lines are not on the same side!
    This results in triples where we have the central and rightmost line from the tuples section plus one line that is at least 2*min and at most 2*max outer angle away from the rightmost line (or vv.)

    Args:
        point (_type_): The road point we want to check for possible anchors
        line_candidate (_type_): _description_
        anchor_trees (_type_): _description_
        max_anchor_distance (_type_): _description_
        max_outer_anchor_angle (_type_): Max angle between right and left line
        min_outer_anchor_angle (_type_): Minimum angle between right and left line
        max_center_tree_slope_angle (_type_): Max deviation of center line from slope line

    Returns:
        _type_: _description_
    """
    min_outer_anchor_angle = 20
    max_outer_anchor_angle = 50
    max_center_tree_slope_angle = 3
    max_anchor_distance = 40
    min_anchor_distane = 15

    # 1. get list of possible anchors -> anchor trees
    anchor_trees_working_copy = anchor_trees.copy()

    # 2. check which points are within distance
    anchor_trees_working_copy = anchor_trees_working_copy[
        (anchor_trees_working_copy.geometry.distance(point) <= max_anchor_distance)
        & (anchor_trees_working_copy.geometry.distance(point) >= min_anchor_distane)
    ]

    # 3. create lines to all these possible connections
    if anchor_trees_working_copy.empty or len(anchor_trees_working_copy) < 3:
        return None, None

    possible_anchor_lines = anchor_trees_working_copy.geometry.apply(
        lambda x: LineString([x, point])
    )

    # check if all of those possible lines are within the max deviation to the slope
    possible_anchor_lines = possible_anchor_lines[
        possible_anchor_lines.apply(
            lambda x: geometry_utilities.angle_between(x, line_candidate)
            < max_outer_anchor_angle
        )
    ].to_list()

    if len(possible_anchor_lines) < 3:
        return None, None

    # 4. check first pairs: one within 10-30 angle to the other and one should be <5 degrees to the slope line
    pairwise_angle = [
        (x, y)
        for x, y in itertools.combinations(possible_anchor_lines, 2)
        if min_outer_anchor_angle
        < geometry_utilities.angle_between(x, y)
        < max_outer_anchor_angle
        and (
            geometry_utilities.angle_between(x, line_candidate)
            < max_center_tree_slope_angle
            or geometry_utilities.angle_between(y, line_candidate)
            < max_center_tree_slope_angle
        )
    ]

    # skip if we dont have enough candidates
    if len(pairwise_angle) < 3:
        return None, None

    # 5. check if the third support line is also within correct angle - within 2*min_outer_anch_angle and 2* max_anchor_angle for one point and within min_outer, max_outer for the other -> so two lines are not on the same side!
    triple_angle = []
    max_supported_force = []
    for x, y in pairwise_angle:
        for z in possible_anchor_lines:
            # make sure that we are not comparing a possible line with itself
            if x is not z and y is not z:
                a = (
                    max_outer_anchor_angle * 2 - 10
                    < geometry_utilities.angle_between(x, z)
                    < max_outer_anchor_angle * 2
                    and min_outer_anchor_angle
                    < geometry_utilities.angle_between(y, z)
                    < max_outer_anchor_angle
                )
                b = max_outer_anchor_angle * 2 - 10 < geometry_utilities.angle_between(
                    y, z
                ) < max_outer_anchor_angle * 2 and min_outer_anchor_angle < geometry_utilities.angle_between(
                    x, z
                )

                if (a, b):
                    triple_angle.append([x, y, z])

                    # find the line with the smallest angle
                    degrees = [
                        geometry_utilities.angle_between(line, line_candidate)
                        for line in [x, y, z]
                    ]
                    center_line = triple_angle[-1][degrees.index(min(degrees))]

                    # get its end tree and retrive its BHD from the DF
                    this_center_tree_bhd = anchor_trees_working_copy[
                        anchor_trees_working_copy.geometry
                        == Point(center_line.coords[0])
                    ]["BHD"].tolist()[0]

                    # compute the max supported force based on the BHD of the center tree
                    security_factor = 5
                    this_max_supported_force = (
                        (this_center_tree_bhd**2) * 10 / security_factor
                    )

                    max_supported_force.append(this_max_supported_force)

    return triple_angle, max_supported_force


def generate_support_trees(
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
    candidate: gpd.GeoDataFrame,
    start_point: Point,
    end_point: Point,
    candidate_point: Point,
    overall_trees,
) -> tuple[Point, LineString, LineString]:
    # 5. create the new candidate point and lines to/from it
    road_to_support_line = LineString([start_point, candidate_point])
    support_to_anchor_line = LineString([candidate_point, end_point])
    # get the location of the support point - why am Ii not able to do this with intermediate_support_candidates?
    new_support_point = overall_trees.iloc[candidate].geometry
    road_to_support_line = LineString([start_point, new_support_point])
    support_to_anchor_line = LineString([new_support_point, end_point])

    return new_support_point, road_to_support_line, support_to_anchor_line


def setup_support_candidates(
    this_cable_road, overall_trees, current_supports, possible_line
):
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
        (overall_trees.distance(possible_line) < 2)
        & (overall_trees.distance(possible_line) > 0.5)
    ]

    # 3. stop if there are no candidates, also stop if we have more than four supports - not viable
    if len(intermediate_support_candidates) < 1 or current_supports > 3:
        return False

    # 4. enumerate through list of candidates - sort by distance to the point of contact
    point_of_contact = this_cable_road.points_along_line[
        sloped_line_to_floor_distances_index
    ]
    distance_candidates = intermediate_support_candidates.distance(point_of_contact)
    distance_candidates = distance_candidates.sort_values(ascending=True)

    return distance_candidates


def create_sideways_cableroads(overall_trees, this_cable_road, candidate, height_gdf):
    """Create the sideways cable roads and return them

    Args:
        overall_trees (_type_): _description_
        this_cable_road (_type_): _description_
        candidate (_type_): _description_
        height_gdf (_type_): _description_

    Returns:
        _type_: _description_
    """
    # need to add the height and force per tree here
    candidate_tree = overall_trees.iloc[candidate]

    # create lines and points left and right
    (
        new_support_point,
        road_to_support_line,
        support_to_anchor_line,
    ) = create_candidate_points_and_lines(
        candidate,
        this_cable_road.start_point,
        this_cable_road.end_point,
        candidate_tree.geometry,
        overall_trees,
    )

    # create left and right sub_cableroad
    road_to_support_cable_road = compute_initial_cable_road(
        road_to_support_line,
        height_gdf,
        initial_tension=this_cable_road.s_current_tension,
    )
    support_to_anchor_cable_road = compute_initial_cable_road(
        support_to_anchor_line,
        height_gdf,
        initial_tension=this_cable_road.s_current_tension,
    )

    return road_to_support_cable_road, support_to_anchor_cable_road, candidate_tree


def test_collisions_left_right(
    cable_roads,
    current_supports,
    location_supports,
    overall_trees,
    pos,
    plot_possible_lines,
    view,
    anchor_triplets,
    max_supported_forces,
    height_gdf,
):
    """test if the left and right cr have collisions and return the new support locations if not

    Args:
        road_to_support_cable_road (_type_): _description_
        support_to_anchor_cable_road (_type_): _description_
        current_supports (_type_): _description_
        location_supports (_type_): _description_
        overall_trees (_type_): _description_
        pos (_type_): _description_
        plot_possible_lines (_type_): _description_
        view (_type_): _description_
        anchor_triplets (_type_): _description_
        max_supported_forces (_type_): _description_
        height_gdf (_type_): _description_

    Returns:
        False, False or current_supports, location_supports
    """

    for cable_road in cable_roads:
        mechanical_computations.check_if_no_collisions_segments(cable_road)
        if not cable_road.no_collisions:
            current_supports, location_supports = compute_required_supports(
                cable_road.line,
                anchor_triplets,
                max_supported_forces,
                height_gdf,
                current_supports,
                plot_possible_lines,
                view,
                location_supports,
                overall_trees,
                pos,
                cable_road.s_current_tension,
            )
            print("current supports", current_supports)
            # if we have more than 4 supports or didnt find any, we stop
            if current_supports and current_supports > 4 or not current_supports:
                return False, False

    return current_supports, location_supports