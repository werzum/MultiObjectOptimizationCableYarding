import numpy as np
import math
import warnings


def unit_vector(vector):
    """ Returns the unit vector of the vector.  """
    return vector / np.linalg.norm(vector)


def angle_between(v1, v2):
    """Returns the angle between to vectors. Returns 0 to 180 degrees angles - note that the direction of the vector matters!
    Will however not discern between a -20 and 20 rotation wrt the v1.

    Args:
        v1 (_type_): _description_
        v2 (_type_): _description_

    Returns:
        _type_: _description_
    """    
    # extract their coords for vector
    v1 = [v1.coords[0], v1.coords[1]]
    v2 = [v2.coords[0], v2.coords[1]]

    # and recompute as vector
    # thanks for reminding me https://discuss.codechef.com/t/how-to-find-angle-between-two-lines/14516
    v1 = (v1[1][0]-v1[0][0], v1[1][1]-v1[0][1])
    v2 = (v2[1][0]-v2[0][0], v2[1][1]-v2[0][1])

    # get the unit vector, dot product and then the arccos from that
    unit_vector_1, unit_vector_2 = unit_vector(v1), unit_vector(v2)
    dot_product = np.dot(unit_vector_1, unit_vector_2)

    if -1 < dot_product < 1:
        # in radians
        angle = np.arccos(dot_product)
        # to degrees - https://stackoverflow.com/questions/9875964/how-can-i-convert-radians-to-degrees-with-python
        return math.degrees(angle)
    else:
        #we return a large angle?
        return 90



def within_maximum_rotation(angle, max_deviation):
    """Check if the angle between the slope line and possible line is too great.
    This checks several cases, but the angles don't seem to be <20 anyways really.

    Returns:
        Truth Value: If the rotation is within the max deviation
    """
    # if angle is smaller than max_dev or greater than 360-max_dev
    condition1 = True if angle < max_deviation or angle > 360-max_deviation else False
    # if flipped line is less than max_deviation+180
    condition2 = True if (180-max_deviation) < angle < 180 + \
        max_deviation else False

    return condition1 or condition2


def area_contains(area, point):
    return area.contains(point)


def create_buffer(geometry, buffer_size):
    return geometry.buffer(buffer_size)


