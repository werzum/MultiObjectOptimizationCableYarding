from shapely.geometry import LineString, Point
import numpy as np
import geopandas as gpd


class Point_3D:
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z

    @property
    def xyz(self):
        return np.array([self.x, self.y, self.z])

    def distance(self, other):
        """Returns the distance between two 3dpoints"""
        return np.sqrt(
            (self.x - other.x) ** 2 + (self.y - other.y) ** 2 + (self.z - other.z) ** 2
        )


class LineString_3D:
    def __init__(self, start_point: Point_3D, end_point: Point_3D):
        self.start_point = start_point
        self.end_point = end_point

    def interpolate(self, distance: float) -> Point_3D:
        """Returns the interpolated point at a given distance from the start point
        Args:
            distance (float): distance from the start point
        Returns:
            Point_3D: interpolated point"""

        vector = self.end_point.xyz - self.start_point.xyz
        # normalize the vector
        vector = vector / np.linalg.norm(vector)
        # multiply the vector with the distance
        vector = vector * distance
        # add the vector to the start point
        return Point_3D(
            self.start_point.x + vector[0],
            self.start_point.y + vector[1],
            self.start_point.z + vector[2],
        )

    def length(self):
        return self.start_point.distance(self.end_point)


class Support:
    def __init__(
        self,
        attachment_height: int,
        xy_location: Point,
        height_gdf: gpd.GeoDataFrame,
        max_supported_force: list[float],
        max_deviation: float = 1,
        max_holding_force: float = 0.0,
        is_tower: bool = False,
    ):
        self.attachment_height: int = attachment_height
        self.xy_location: Point = xy_location
        self.xy_location_numpy = np.array(self.xy_location.xy).T
        self.max_deviation: float = max_deviation
        self.is_tower: bool = is_tower
        self.max_supported_force = max_supported_force
        self.max_holding_force: float = max_holding_force

        # get the elevation of the floor below the support
        self.floor_height = geometry_operations.fetch_point_elevation(
            self.xy_location, height_gdf, self.max_deviation
        )

    @property
    def total_height(self):
        return self.floor_height + self.attachment_height

    @property
    def xyz_location(self):
        return Point_3D(self.xy_location.x, self.xy_location.y, self.total_height)

    @property
    def max_supported_force_at_attachment_height(self):
        return self.max_supported_force[self.attachment_height]

    @max_supported_force_at_attachment_height.setter
    def max_supported_force_at_attachment_height(self, value):
        self.max_supported_force[self.attachment_height] = value


class Cable_Road:
    def __init__(
        self,
        line,
        height_gdf,
        start_support: Support,
        end_support: Support,
        pre_tension: float = 0,
        number_sub_segments: int = 0,
    ):
        self.start_support: Support = start_support
        self.end_support: Support = end_support

        """heights"""
        self.floor_height_below_line_points = (
            []
        )  # the elevation of the floor below the line
        self.sloped_line_to_floor_distances = np.array([])
        self.unloaded_line_to_floor_distances = np.array([])
        """ geometry features """
        self.line = line
        # self.start_point = Point(line.coords[0])
        # self.end_point = Point(line.coords[1])
        self.points_along_line = []
        self.floor_points = []
        self.max_deviation = 1
        self.anchor_triplets = []
        """ Fixed cable road parameters """
        self.q_s_self_weight_center_span = 10
        self.q_load = 80000
        self.b_length_whole_section = 0.0
        self.s_max_maximalspannkraft = 0.0
        """ Modifiable collision parameters """
        self.no_collisions = True
        self.anchors_hold = True

        # Parameters to keep track of segments+
        self.number_sub_segments = number_sub_segments
        self.supported_segments: list[
            SupportedSegment
        ] = []  # list of SupportedSegment objects, ie. sub cable roads

        self._s_current_tension = 0.0
        print(
            "Cable road created from line: ",
            self.line.coords[0],
            "to ",
            self.line.coords[1],
        )

        # fetch the floor points along the line - xy view
        self.points_along_line = geometry_operations.generate_road_points(
            self.line, interval=1
        )

        self.points_along_line_x, self.points_along_line_y = [
            point.x for point in self.points_along_line
        ], [point.y for point in self.points_along_line]

        self.points_along_line_xy = np.column_stack(
            (self.points_along_line_x, self.points_along_line_y)
        )

        # get the height of those points and set them as attributes to the CR object
        self.compute_floor_height_below_line_points(height_gdf)
        # generate floor points and their distances
        self.floor_points = list(
            zip(
                self.points_along_line_x,
                self.points_along_line_y,
                self.floor_height_below_line_points,
            )
        )

        self.b_length_whole_section = self.start_support.xy_location.distance(
            self.end_support.xy_location
        )

        self.c_rope_length = self.start_support.xyz_location.distance(
            self.end_support.xyz_location
        )

        self.initialize_line_tension(number_sub_segments, pre_tension)

    @property
    def line_to_floor_distances(self):
        return np.asarray(
            [
                geometry_utilities.lineseg_dist(
                    point,
                    self.start_support.xyz_location.xyz,
                    self.end_support.xyz_location.xyz,
                )
                for point in self.floor_points
            ]
        )

    @property
    def absolute_unloaded_line_height(self):
        return (
            self.floor_height_below_line_points + self.unloaded_line_to_floor_distances
        )

    @property
    def absolute_loaded_line_height(self):
        return self.floor_height_below_line_points + self.sloped_line_to_floor_distances

    @property
    def rope_points_xyz(self):
        return list(
            zip(
                self.points_along_line_x,
                self.points_along_line_y,
                self.absolute_loaded_line_height,
            )
        )

    @property
    def s_current_tension(self) -> float:
        return self._s_current_tension

    # ensure that the CR height is updated when we change the tension
    @s_current_tension.setter
    def s_current_tension(self, value):
        self._s_current_tension = value
        self.compute_loaded_unloaded_line_height()

        # also for the sub-CRs
        if self.supported_segments:
            for segment in self.supported_segments:
                segment.cable_road.s_current_tension = value
                segment.cable_road.compute_loaded_unloaded_line_height()

    def count_segments(self, number_sub_segments: int = 0) -> int:
        """recursively counts the number of segments in the cable road"""
        if self.supported_segments:
            number_sub_segments += 2
            for segment in self.supported_segments:
                number_sub_segments = segment.cable_road.count_segments(
                    number_sub_segments
                )

        return number_sub_segments

    def get_all_subsegments(self):
        """get a generator of all subsegments of the cable road
        Loosely based on https://stackoverflow.com/questions/10823877/what-is-the-fastest-way-to-flatten-arbitrarily-nested-lists-in-python

        Returns:
            generator: generator of all subsegments (apply list to get list of it)

        """
        for i in self.supported_segments:
            if i.cable_road.supported_segments:
                yield from i.cable_road.get_all_subsegments()
            else:
                yield i

    def compute_floor_height_below_line_points(self, height_gdf: gpd.GeoDataFrame):
        """compute the height of the line above the floor as well as the start and end point in 3d. Query the global kdtree for that
        Sets the floor_height_below_line_points and the line_start_point_array and line_end_point_array
        Args:
            height_gdf (gpd.GeoDataFrame): the floor height data
        """
        d, i = global_vars.kdtree.query(
            list(zip(self.points_along_line_x, self.points_along_line_y))
        )
        # Use the final condition to filter the height_gdf and get the elev values
        self.floor_height_below_line_points = height_gdf.iloc[i]["elev"].values

    def compute_loaded_unloaded_line_height(self):
        """compute the loaded and unloaded line to floor distances"""
        self.calculate_cr_deflections(loaded=True)
        self.calculate_cr_deflections(loaded=False)

    def calculate_cr_deflections(self, loaded: bool = True):
        """calculate the deflections of the CR line due to the load, either loaded or unlaoded
        Args:
            loaded (bool, optional): whether the line is loaded or not. Defaults to True.
        """

        y_x_deflections = mechanical_computations.pestal_load_path(self, loaded)

        if loaded:
            self.sloped_line_to_floor_distances = (
                self.line_to_floor_distances - y_x_deflections
            )
        else:
            self.unloaded_line_to_floor_distances = (
                self.line_to_floor_distances - y_x_deflections
            )

    def initialize_line_tension(self, current_supports: int, pre_tension: int = 0):
        # set tension of the cable_road
        s_br_mindestbruchlast = 170000  # in newton
        self.s_max_maximalspannkraft = s_br_mindestbruchlast / 2
        if pre_tension:
            self.s_current_tension = pre_tension
        else:
            self.s_current_tension = self.s_max_maximalspannkraft


class SupportedSegment:
    def __init__(
        self,
        cable_road: Cable_Road,
        start_support: Support,
        end_support: Support,
    ):
        self.cable_road = cable_road
        self.start_support = start_support
        self.end_support = end_support


# Helper Functions for setting up the cable road


def initialize_cable_road_with_supports(
    line: LineString,
    height_gdf: gpd.GeoDataFrame,
    start_point_max_supported_force: list[float],
    end_point_max_supported_force: list[float],
    pre_tension=0,
    is_tower=False,
):
    start_support = Support(
        attachment_height=11,
        xy_location=Point(line.coords[0]),
        height_gdf=height_gdf,
        max_supported_force=start_point_max_supported_force,
        is_tower=is_tower,
    )
    end_support = Support(
        attachment_height=8,
        xy_location=Point(line.coords[-1]),
        height_gdf=height_gdf,
        max_supported_force=end_point_max_supported_force,
        is_tower=False,
    )
    return Cable_Road(line, height_gdf, start_support, end_support, pre_tension)


def load_cable_road(line_gdf: gpd.GeoDataFrame, index: int) -> Cable_Road:
    """Helper function to abstract setting up a sample cable road from the line_gdf"""
    return line_gdf.iloc[index]["Cable Road Object"]


from src.main import (
    geometry_operations,
    geometry_utilities,
    mechanical_computations,
    global_vars,
)