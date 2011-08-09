import psycopg2, re, os, time, urllib2
from osgeo import gdal, ogr
from math import sqrt, floor, fabs
   
   
def build_postgis_poly_box(bbox,srid):
    return "ST_GeomFromText('POLYGON((%s %s,%s %s,%s %s,%s %s,%s %s))',%s)" % (str(bbox[0][0]), str(bbox[0][1]), str(bbox[1][0]), str(bbox[0][1]), str(bbox[1][0]), str(bbox[1][1]), str(bbox[0][0]), str(bbox[1][1]), str(bbox[0][0]), str(bbox[0][1]), srid)


def find_matching_tiles(bbox,srid,imagery_coverage_table,postgis_connection):    
    bbox_constructor = build_postgis_poly_box(bbox,srid)

    intersect_query = "SELECT distinct gnis FROM %s WHERE ST_Intersects(%s,the_geom) = True;" % (imagery_coverage_table, bbox_constructor)
    
    # Open a psycopg2 cursor to perform database operations
    match_cursor = postgis_connection.cursor()

    match_cursor.execute(intersect_query)

    result_set = match_cursor.fetchall()
    
    matches = []
    
    print "%s matches found:" % str(len(result_set))
    for record in result_set:
        cell = record[0]
        
        #get folder name out of file name
        folder_name = re.search('([0-9]+)',cell).group()
        
        matches.append((folder_name, "m" + cell + "nw.tif")) #Add folder, file to matching images
        matches.append((folder_name, "m" + cell + "ne.tif")) #Add folder, file to matching images
        matches.append((folder_name, "m" + cell + "se.tif")) #Add folder, file to matching images
        matches.append((folder_name, "m" + cell + "sw.tif")) #Add folder, file to matching images
        
    match_cursor.close()
    return matches
    
def download_tiles(tile_list,remote_root_directory,local_destination_root,overwrite_files=False):
    try:
        #Check if remote folder exists
        try:
            urllib2.urlopen(remote_root_directory)
            remote_exists = True
        except:
            print "Remote directory doesn't exist, or you don't have a valid Internet connection."
            remote_exists = False
        
        if remote_exists:
                    
            #Check if destination root exists.
            if os.path.isdir(local_destination_root):
    
                for tile in tile_list:
                    try:
                        #Check if destination subdirectory exists. Create it if it doesn't.
                        test_subdirectory = os.path.join(local_destination_root,tile[0])
                        if not os.path.exists(test_subdirectory):
                            print "Creating subdirectory /%s" % str(tile[0])
                            os.makedirs(test_subdirectory)
                            
                        #Check if this file already exists locally.
                        local_path = os.path.join(local_destination_root,tile[0],tile[1])
                        if os.path.exists(local_path) and overwrite_files == False:
                            print "File exists locally already. Skipping. You can override this, BTW."
                        else:
                            print 'Downloading %s of %s: %s/%s' % (tile_list.index(tile)+1,len(tile_list),tile[0],tile[1])
                            remote_file = os.path.join(remote_root_directory,tile[0],tile[1])
                            print remote_file
                            geotiff = urllib2.urlopen(remote_file)
                            output = open(local_path,'wb')
                            output.write(geotiff.read())
                            output.close()
                    except:
                        print 'Error ...'
                    time.sleep(5)
                    
                return True
                
            else:
                print "Your destination directory doesn't exist. Please check and try again."
                return False
    
    except:
        print "Download process failed"
        return False
    

def create_clipping_shp(bbox,temp_folder,temp_table_name,srid,postgis_connection,db_name):
    clipping_shapefile = False
    # Open a psycopg2 cursor to perform database operations
    clipping_cursor = postgis_connection.cursor()
    try:
        bbox_constructor = build_postgis_poly_box(bbox,srid)
    
        clipping_cursor.execute("CREATE TABLE " + temp_table_name + " (gid int4 CONSTRAINT firstkey PRIMARY KEY, featname varchar(40));")

        try:
            clipping_cursor.execute("SELECT AddGeometryColumn ('public','" + temp_table_name + "','the_geom'," + srid + ",'POLYGON',2);")
            
            try:
                clipping_cursor.execute("INSERT INTO " + temp_table_name + " VALUES (1, 'zoomtest', " + bbox_constructor + ");")
                postgis_connection.commit()
                
                try:
                    #folder for clipping shapefiles and resized images
                    temp_export_path = os.path.join(temp_folder,temp_table_name)
                    os.makedirs(temp_export_path)
                    
                    os.system("pgsql2shp -f " + temp_export_path + "/" + temp_table_name + " "+ db_name + " public." + temp_table_name)
                    
                    clipping_shapefile = os.path.join(temp_export_path, "%s.shp" % (temp_table_name))
                    
                except:
                    print 'Failed to export temporary shapefile for clipping.'
                    return False
                    
                #Remove geometry columns entry, delete temporary table
                clipping_cursor.execute("DELETE FROM geometry_columns WHERE f_table_name LIKE '" + temp_table_name + "';")
                clipping_cursor.execute("DROP TABLE " + temp_table_name + ";")
                postgis_connection.commit()
                clipping_cursor.close()
                
                return clipping_shapefile
            except:
                print 'Failed to insert record into temp table.'
                return False
        except:
            print 'Failed to register a geometry column for the temporary table. Please verify that your database is a standard PostGIS-enabled DB with a geometry_columns table.'
            return False
    except:
        print 'Couldn\'t create a temporary PostGIS table. Please insure your user has the appropriate permissions to create tables. Alternatively, you might have a pre-existing table called the same thing, which would be a little unexpected. Either I screwed up or you have strange naming conventions.'
        return False
    

def calculate_shrink_percentage(test_image,matching_images,max_stitched_width,max_stitched_height,clipping_shp_extent):
    try:
        if os.path.isfile(test_image):
            
            ds = gdal.Open(test_image)
            tile_pixel_width = ds.RasterXSize
            tile_pixel_height = ds.RasterYSize
            
            #Get the width of the tile in map units. We do this by using GDAL's GetGeoTransform()
            #Reference http://gdal.org/gdal_datamodel.html
            tile_geo_transform = ds.GetGeoTransform()
            
            tile_minx = tile_geo_transform[0]
            tile_miny = tile_geo_transform[3] + tile_pixel_width*tile_geo_transform[4] + tile_pixel_height*tile_geo_transform[5]
            tile_maxx = tile_geo_transform[0] + tile_pixel_width*tile_geo_transform[1] + tile_pixel_height*tile_geo_transform[2]
            tile_maxy = tile_geo_transform[3] 
            
            tile_geo_width = fabs(tile_maxx-tile_minx)
            tile_geo_height = fabs(tile_maxy-tile_miny)
            
            #compare to map-unit width/height of the clipping shapefile
            clipping_shp_geo_width = fabs(clipping_shp_extent[1] - clipping_shp_extent[0])
            clipping_shp_geo_height = fabs(clipping_shp_extent[3] - clipping_shp_extent[2])
            
            #Compare the map-unit spread of the file to the desired pixel and geo spreads
            desired_tile_width = (tile_geo_width*max_stitched_width)/clipping_shp_geo_width
            
            reduce_percentage = round(desired_tile_width/tile_pixel_width,4)*100
            if reduce_percentage > 100:
                reduce_percentage = 100
            
            print "Reduction percentage: %s" % str(reduce_percentage)
            return reduce_percentage
            
        else:
            print "No image found to check reduction percentage. Please make sure your imagery is the correct location."
    
    except:
        print "Failed to calculate image resize percentages"
        return False
 

#Resize a list of GDAL-compatible images to a given percentage
def shrink_files(files_list, tile_shrunk_dir, size_percentage):
    try:
        shrunken_images = []
        if not os.path.exists(tile_shrunk_dir):
            os.makedirs(tile_shrunk_dir)
            
            print "Number of files in shrink list: %s" % str(len(files_list))
            
        for f in files_list:
            local_file = os.path.splitext(f)
            
            target_file_path = os.path.join(tile_shrunk_dir,os.path.basename(f))
    
            translate_line = 'gdal_translate ' + f + ' ' + target_file_path + ' -outsize ' + str(size_percentage) + '% ' + str(size_percentage) + '% -b 1 -b 2 -b 3 -mask 4 -co COMPRESS=JPEG -co PHOTOMETRIC=YCBCR --config GDAL_TIFF_INTERNAL_MASK YES'
            
            os.system(translate_line)
            
            shrunken_images.append(target_file_path)
            print 'Resized %s' % target_file_path
        
        return shrunken_images
    
    except:
        print "Shrink failure"
        return False


#Merges tiles, then clips to a shapefile clipping mask      
def merge_clip_file(files_list,clipping_shapefile,target_file):
    try:
        files_list_string = ' '.join(files_list)
        
        warp_line = 'gdalwarp -srcnodata "0" -crop_to_cutline -cutline ' + clipping_shapefile + ' ' + files_list_string + ' ' + target_file
    
        print 'Merging and clipping final image'
    
        os.system(warp_line)
        
        return target_file
    except:
        print "Merge/clip failure"
        return False



def zoom_stitcher(bounding_boxes,srid,imagery_coverage_table,render_location,local_imagery_root,postgis_connection,postgis_db,max_stitched_width,max_stitched_height):
    try:
        matching_imagery = []
        
        #test if destination exists before you get too far into the heavy stuff
        #print os.path.isdir(strRenderDestination)
        if os.path.isdir(render_location):
        
            #make master directory to store renders
            destination_root = os.path.join(render_location,'zoomstitcher_output')
            if not os.path.exists(destination_root):
                os.makedirs(destination_root)
                
                #make a folder for files created during the stitching process
                destination_temp = os.path.join(destination_root,'intermediate_files')
                os.makedirs(destination_temp)
                
            else:
                print "\n\nHmmm. There's already a directory in your destination directory that looks like a zoomstitcher render folder. Please rename or delete it and try again.\n\n"
    
    
        # For each bounding box, find the cells in your coverage table that match the bounding box
        for bbox in bounding_boxes:
            print "Searching for matches to bounding box %s of %s" % (str(bounding_boxes.index(bbox)+1),str(len(bounding_boxes)))
            
            zoom_level = bounding_boxes.index(bbox)+1
            
            these_matches = find_matching_tiles(bbox,srid,imagery_coverage_table,postgis_connection)
            
            if these_matches:
                matching_imagery.append(these_matches) #Create new list of matching images for this bounding box
                    
                #test if destination exists before you get too far into the heavy stuff
                if os.path.isdir(render_location):
                    
                    if os.path.isdir(destination_root):
                        #for zoom_level_matches in matching_imagery:
                            
                        #use shp2pgsql to convert bounding boxes to shapefile for use as cutout. Doing this now so you don't crap out after heavy rendering begins.
                        
                        #generate temp table/shapefile name for each bounding box
                        temp_table_name = 'zoomstitchtemp%s' % str(round(time.time()*100))[-8:-2]
                        
                        clipping_shp = create_clipping_shp(bbox,destination_temp,temp_table_name,srid,postgis_connection,postgis_db)
                        
                        if clipping_shp:
                             
                            #use first image in these_matches and the extent of your clipping shapefile to figure out the correct percentage to shrink to
                            first_image = os.path.join(local_imagery_root,these_matches[0][0],these_matches[0][1])
                            
                            #get geo width/height of clipping shapefile (in mapping units of whatever projection you happen to be in)
                            ogr_shp = ogr.Open(clipping_shp)
                            ogr_layer = ogr_shp.GetLayer(0)
                            clipping_shp_extent = ogr_layer.GetExtent()
                            
                            shrink_pct = calculate_shrink_percentage(first_image,these_matches,max_stitched_width,max_stitched_height,clipping_shp_extent)
                            
                            print "Shrink percentage: %s" % str(shrink_pct)
                            
                            if shrink_pct:
                            
                                #create a list from all images that actually exist
                                shrink_list = []
                                for tile in these_matches:
                                    tile_path = os.path.join(local_imagery_root,tile[0],tile[1])
                                    if os.path.isfile(tile_path):
                                        shrink_list.append(tile_path)
                            
                                #shrink each file in list
                                merge_target_list = shrink_files(shrink_list, os.path.join(destination_temp, temp_table_name, "%spct" % str(shrink_pct)), shrink_pct)
                                
                                if merge_target_list:
                                    merge_clip_file(merge_target_list,clipping_shp,os.path.join(destination_root,'mergefile_%s.tif' % str(zoom_level)))   
         
            else:
                print 'Problem! Your destination directory doesn\'t seem to be a valid directory.'
    
        # Close communication with the database
        postgis_connection.close()
        return True
    except:
        print "Zoom stitcher failure"
        return False
    