import rasterio
from shapely.geometry import Polygon, Point
import pandas as pd
import geopandas as gpd
from pandas.api.types import is_string_dtype
import numpy as np


def read_tif(file_path):
    """Reads a given tif file and extracts its features as raster

    Args:
        file_path (_type_): Filepath

    Returns:
        List: A list of Polygon features
    """
    # Convert tif to geometry
    geom_array = []
    with rasterio.open(file_path) as dataset:
        # Read the dataset's valid data mask as a ndarray.
        mask = dataset.dataset_mask()

        # Extract feature shapes and values from the array.
        for geom, val in rasterio.features.shapes(mask, transform=dataset.transform):
            geom_array.append(Polygon(geom["coordinates"][0]))

        # stackoverflow magic to read the height values to an array https://stackoverflow.com/questions/51485603/converting-numpy-structured-array-to-pandas-dataframes
        arr = dataset.read(1)
        mask = arr != dataset.nodata
        elev = arr[mask]
        col, row = np.where(mask)
        x, y = dataset.xy(col, row)
        uid = np.arange(dataset.height * dataset.width).reshape(
            (dataset.height, dataset.width)
        )[mask]

    height_df = pd.DataFrame(
        np.rec.fromarrays([uid, x, y, elev], names=["id", "x", "y", "elev"])
    ).set_index("id")

    return geom_array, height_df


def read_csv(file_path):
    with open(file_path) as file:
        bestand = pd.read_csv(file, sep="\t", dtype=str)
    return bestand


def format_csv(csv):
    # ensure the csv columns are parsed correctly - especially with the separators
    # convert just columns "a" and "b"
    csv[["x", "y", "z", "id", "BHD", "crownVolume"]] = (
        csv[["x", "y", "z", "id", "BHD", "crownVolume"]]
        .replace(",", ".", regex=True)
        .astype(float)
    )
    csv[["x", "y", "z", "id", "BHD", "crownVolume"]] = csv[
        ["x", "y", "z", "id", "BHD", "crownVolume"]
    ].apply(pd.to_numeric)
    return csv


def load_bestand_and_forest():
    # load the tif
    tif_shapes, height_df = read_tif("Resources_Organized/tif/Bestand3.tif")

    forest_area_gdf = gpd.GeoDataFrame(
        pd.DataFrame({"name": ["area1", "area2", "area3", "area4", "area5"]}),
        geometry=tif_shapes,
    )

    # load the data and show that we have correctly parsed the CSV
    bestand_3_csv = read_csv("Resources_Organized/csv/Bestand3_h.csv")
    bestand_3_csv = format_csv(bestand_3_csv)

    return forest_area_gdf, bestand_3_csv, height_df


def parse_point(point_string):
    # Remove the "POINT " prefix and parentheses from the string
    point_string = point_string.replace("POINT (", "").replace(")", "")
    # Split the string into two coordinates
    x, y = point_string.split()
    # Convert the coordinates to floats and create a new Point object
    return Point(float(x), float(y))


def parse_list_int(list_string):
    removed_stopsigns = (
        list_string.replace("[", "").replace("]", "").replace(",", "").split()
    )
    parsed_numbers = [int(float(x)) for x in removed_stopsigns]
    return parsed_numbers


def parse_list_float(list_string):
    removed_stopsigns = (
        list_string.replace("[", "").replace("]", "").replace(",", "").split()
    )
    parsed_numbers = [float(x) for x in removed_stopsigns]
    return parsed_numbers


def load_processed_gdfs():
    with open("03_Data/Resources_Organized/Dataframes_Processed/tree_gdf.csv") as file:
        bestand = pd.read_csv(file)

    with open("03_Data/Resources_Organized/Dataframes_Processed/height_df.csv") as file:
        height = pd.read_csv(file)

    # Apply the function to the 'point' column using the apply method
    bestand["geometry"] = bestand["geometry"].apply(parse_point)
    height["geometry"] = height["geometry"].apply(parse_point)

    bestand["height_series"] = bestand["height_series"].apply(parse_list_int)
    bestand["diameter_series"] = bestand["diameter_series"].apply(parse_list_float)

    bestand = gpd.GeoDataFrame(bestand, geometry=bestand["geometry"])
    height = gpd.GeoDataFrame(height, geometry=height["geometry"])

    return bestand, height