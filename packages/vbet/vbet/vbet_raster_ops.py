import os
import shutil
import ogr
from osgeo import gdal
import rasterio
import numpy as np
from rscommons import ProgressBar, Logger, VectorBase, Timer, TempRaster


def rasterize(in_lyr_path, out_raster_path, template_path):
    """Rasterizing an input 

    Args:
        in_lyr_path ([type]): [description]
        out_raster_ ([type]): [description]
        template_path ([type]): [description]
    """
    log = Logger('VBETRasterize')
    ds_path, lyr_path = VectorBase.path_sorter(in_lyr_path)

    progbar = ProgressBar(100, 50, "Rasterizing ")

    with rasterio.open(template_path) as raster:
        t = raster.transform
        raster_bounds = raster.bounds

    def poly_progress(progress, _msg, _data):
        progbar.update(int(progress * 100))

    # Rasterize the features (roads, rail etc) and calculate a raster of Euclidean distance from these features
    progbar.update(0)

    # Rasterize the polygon to a temporary file
    with TempRaster('vbet_rasterize') as tempfile:
        log.debug('Temporary file: {}'.format(tempfile.filepath))
        gdal.Rasterize(
            tempfile.filepath,
            ds_path,
            layers=[lyr_path],
            xRes=t[0], yRes=t[4],
            burnValues=1, outputType=gdal.GDT_Int16,
            creationOptions=['COMPRESS=LZW'],
            # outputBounds --- assigned output bounds: [minx, miny, maxx, maxy]
            outputBounds=[raster_bounds.left, raster_bounds.bottom, raster_bounds.right, raster_bounds.top],
            callback=poly_progress
        )
        progbar.finish()

        # Now mask the output correctly
        mask_rasters_nodata(tempfile.filepath, template_path, out_raster_path)


def mask_rasters_nodata(in_raster_path, nodata_raster_path, out_raster_path):
    """Apply the nodata values of one raster to another of identical size

    Args:
        in_raster_path ([type]): [description]
        nodata_raster_path ([type]): [description]
        out_raster_path ([type]): [description]
    """
    log = Logger('mask_rasters_nodata')

    with rasterio.open(nodata_raster_path) as nd_src, rasterio.open(in_raster_path) as data_src:
        # All 3 rasters should have the same extent and properties. They differ only in dtype
        out_meta = data_src.meta
        out_meta['nodata'] = -9999
        out_meta['compress'] = 'deflate'

        with rasterio.open(out_raster_path, 'w', **out_meta) as out_src:
            progbar = ProgressBar(len(list(data_src.block_windows(1))), 50, "Applying nodata mask")
            counter = 0
            # Again, these rasters should be orthogonal so their windows should also line up
            for ji, window in data_src.block_windows(1):
                progbar.update(counter)
                counter += 1
                # These rasterizations don't begin life with a mask.
                mask = nd_src.read(1, window=window, masked=True).mask
                data = data_src.read(1, window=window)
                # Fill everywhere the mask reads true with a nodata value
                output = np.ma.masked_array(data, mask)
                out_src.write(output.filled(out_meta['nodata']), window=window, indexes=1)

            progbar.finish()
            log.info('Complete')


# Compute Proximity for channel rasters
def proximity_raster(src_raster_path: str, out_raster_path: str, dist_units="PIXEL", preserve_nodata=True):
    """Create a proximity raster

    Args:
        src_raster_path ([type]): [description]
        out_raster_path ([type]): [description]
        dist_units (str, optional): set to "GEO" for distance in length . Defaults to "PIXEL".
    """
    log = Logger('proximity_raster')
    tmr = Timer()
    src_ds = gdal.Open(src_raster_path)
    srcband = src_ds.GetRasterBand(1)

    drv = gdal.GetDriverByName('GTiff')
    with TempRaster('vbet_proximity_raster') as tempfile:
        dst_ds = drv.Create(tempfile.filepath,
                            src_ds.RasterXSize, src_ds.RasterYSize, 1,
                            gdal.GetDataTypeByName('Float32'))

        dst_ds.SetGeoTransform(src_ds.GetGeoTransform())
        dst_ds.SetProjection(src_ds.GetProjectionRef())

        dstband = dst_ds.GetRasterBand(1)

        log.info('Creating proximity raster')
        gdal.ComputeProximity(srcband, dstband, ["VALUES=1", f"DISTUNITS={dist_units}", "COMPRESS=DEFLATE"])

        srcband = None
        dstband = None
        src_ds = None
        dst_ds = None

        # Preserve the nodata from the source
        if preserve_nodata:
            mask_rasters_nodata(tempfile.filepath, src_raster_path, out_raster_path)
        else:
            shutil.copyfile(tempfile.filepath, out_raster_path)

        log.info('completed in {}'.format(tmr.toString()))


def translate(vrtpath_in: str, raster_out_path: str, band: int):
    """GDAL translate Operation from VRT 

    Args:
        vrtpath_in ([type]): [description]
        raster_out_path ([type]): [description]
        band ([int]): raster band
    """
    log = Logger('translate')
    tmr = Timer()
    progbar = ProgressBar(100, 50, "Translating ")

    def translate_progress(progress, _msg, _data):
        progbar.update(int(progress * 100))

    translateoptions = gdal.TranslateOptions(gdal.ParseCommandLine("-of Gtiff -b {} -co COMPRESS=DEFLATE".format(band)))
    gdal.Translate(raster_out_path, vrtpath_in, options=translateoptions, callback=translate_progress)

    log.info('completed in {}'.format(tmr.toString()))


def inverse_mask(nodata_raster_path, out_raster_path):
    """Apply the nodata values of one raster to another of identical size

    Args:
        in_raster_path ([type]): [description]
        nodata_raster_path ([type]): [description]
        out_raster_path ([type]): [description]
    """
    log = Logger('mask_rasters_nodata')

    with rasterio.open(nodata_raster_path) as nd_src:
        # All 3 rasters should have the same extent and properties. They differ only in dtype
        out_meta = nd_src.meta
        if 'nodata' not in out_meta or out_meta['nodata'] is None:
            out_meta['nodata'] = -9999
        out_meta['compress'] = 'deflate'

        with rasterio.open(out_raster_path, 'w', **out_meta) as out_src:
            progbar = ProgressBar(len(list(nd_src.block_windows(1))), 50, "Applying inverse nodata mask")
            counter = 0
            # Again, these rasters should be orthogonal so their windows should also line up
            for ji, window in nd_src.block_windows(1):
                progbar.update(counter)
                counter += 1
                # These rasterizations don't begin life with a mask.
                mask = nd_src.read(1, window=window, masked=True).mask
                # Fill everywhere the mask reads true with a nodata value
                mask_vals = np.full(mask.shape, 1)
                output = np.ma.masked_array(mask_vals, np.logical_not(mask))
                out_src.write(output.filled(out_meta['nodata']).astype(out_meta['dtype']), window=window, indexes=1)

            progbar.finish()
            log.info('Complete')


def raster_clean(in_raster_path: str, out_raster_path: str, buffer_pixels=1):
    """This method grows and shrinks the raster by n pixels

    Args:
        in_raster_path (str): [description]
        out_raster_path (str): [description]
        buffer_pixels (int, optional): [description]. Defaults to 1.
    """

    log = Logger('raster_clean')

    with TempRaster('vbet_clean_prox_out') as tmp_prox_out, \
            TempRaster('vbet_clean_buff_out') as tmp_buff_out, \
            TempRaster('vbet_clean_prox_in') as tmp_prox_in, \
            TempRaster('vbet_clean_mask') as inv_mask:

        # 1. Find the proximity raster
        proximity_raster(in_raster_path, tmp_prox_out.filepath, preserve_nodata=False)

        # 2. Logical and the prox > 1 with the mask for the input raster
        with rasterio.open(tmp_prox_out.filepath) as prox_out_src, \
                rasterio.open(in_raster_path) as in_data_src:

            # All 3 rasters should have the same extent and properties. They differ only in dtype
            out_meta = in_data_src.meta
            # Rasterio can't write back to a VRT so rest the driver and number of bands for the output
            out_meta['driver'] = 'GTiff'
            out_meta['count'] = 1
            out_meta['compress'] = 'deflate'

            with rasterio.open(tmp_buff_out.filepath, 'w', **out_meta) as out_data:
                progbar = ProgressBar(len(list(out_data.block_windows(1))), 50, "Growing the raster by {} pixels".format(buffer_pixels))
                counter = 0
                # Again, these rasters should be orthogonal so their windows should also line up
                for _ji, window in out_data.block_windows(1):
                    progbar.update(counter)
                    counter += 1
                    prox_out_block = prox_out_src.read(1, window=window)
                    in_data_block = in_data_src.read(1, window=window, masked=True)

                    new_data = np.full(in_data_block.shape, 1)
                    new_mask = np.logical_and(in_data_block.mask, prox_out_block > buffer_pixels)

                    output = np.ma.masked_array(new_data, new_mask)

                    out_data.write(output.filled(out_meta['nodata']).astype(out_meta['dtype']), window=window, indexes=1)

                progbar.finish()

        # 3. Invert the product of (2) and find the inwards proximity
        inverse_mask(tmp_buff_out.filepath, inv_mask.filepath)
        proximity_raster(inv_mask.filepath, tmp_prox_in.filepath, preserve_nodata=False)

        # 4. Now do the final logical and to shrink back a pixel
        with rasterio.open(tmp_prox_in.filepath) as prox_in_src:
            # Note: we reuse outmeta from before

            with rasterio.open(out_raster_path, 'w', **out_meta) as out_data_src:
                progbar = ProgressBar(len(list(out_data.block_windows(1))), 50, "Shrinking the raster by {} pixels".format(buffer_pixels))
                counter = 0
                # Again, these rasters should be orthogonal so their windows should also line up
                for _ji, window in out_data.block_windows(1):
                    progbar.update(counter)
                    counter += 1
                    prox_in_block = prox_in_src.read(1, window=window)

                    new_data = np.full(prox_in_block.shape, 1)
                    new_mask = np.logical_not(prox_in_block > buffer_pixels)

                    output = np.ma.masked_array(new_data, new_mask)
                    out_data_src.write(output.filled(out_meta['nodata']).astype(out_meta['dtype']), window=window, indexes=1)

                progbar.finish()

        log.info('Cleaning finished')


def rasterize_attribute(in_lyr_path, out_raster_path, template_path, attribute_field):
    """Rasterizing an input 

    Args:
        in_lyr_path ([type]): [description]
        out_raster_ ([type]): [description]
        template_path ([type]): [description]
    """
    log = Logger('VBETRasterize')
    ds_path, lyr_path = VectorBase.path_sorter(in_lyr_path)

    progbar = ProgressBar(100, 50, "Rasterizing ")

    # with rasterio.open(template_path) as raster:
    #     t = raster.transform
    #     raster_bounds = raster.bounds
    #     ncol = raster.width
    #     nrow = raster.height

    raster_template = gdal.Open(template_path)
    # Fetch number of rows and columns
    ncol = raster_template.RasterXSize
    nrow = raster_template.RasterYSize
    # Fetch projection and extent
    proj = raster_template.GetProjectionRef()
    ext = raster_template.GetGeoTransform()
    raster_template = None

    def poly_progress(progress, _msg, _data):
        progbar.update(int(progress * 100))

    # Rasterize the features (roads, rail etc) and calculate a raster of Euclidean distance from these features
    progbar.update(0)

    src_driver = ogr.GetDriverByName('GPKG')
    src_ds = src_driver.Open(ds_path)
    src_lyr = src_ds.GetLayer(lyr_path)

    # Rasterize the polygon to a temporary file
    with TempRaster('vbet_rasterize_attribute') as tempfile:
        log.debug('Temporary file: {}'.format(tempfile.filepath))

        driver = gdal.GetDriverByName('GTiff')
        out_raster = driver.Create(tempfile.filepath, ncol, nrow, 1, gdal.GDT_Int16)
        out_raster.SetProjection(proj)
        out_raster.SetGeoTransform(ext)
        options = ['ALL_TOUCHED=TRUE', f'ATTRIBUTE={attribute_field}']
        gdal.RasterizeLayer(out_raster, [1], src_lyr, options=options)

        # gdal.Rasterize(
        #     tempfile.filepath,
        #     ds_path,
        #     layers=[lyr_path],
        #     xRes=t[0], yRes=t[4],
        #     attribute=attribute_field, outputType=gdal.GDT_Int32,
        #     creationOptions=['COMPRESS=LZW'],
        #     # outputBounds --- assigned output bounds: [minx, miny, maxx, maxy]
        #     outputBounds=[raster_bounds.left, raster_bounds.bottom, raster_bounds.right, raster_bounds.top],
        #     callback=poly_progress
        # )
        out_raster = None
        progbar.finish()

        # Now mask the output correctly
        mask_rasters_nodata(tempfile.filepath, template_path, out_raster_path)
