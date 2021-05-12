#!/bin/bash
set -eu
IFS=$'\n\t'

# These environment variables need to be present before the script starts
(: "${HUC?}")
(: "${PROGRAM?}")
(: "${RS_CONFIG?}")
(: "${RSCONTEXT_TAGS?}")
(: "${VBET_TAGS?}")

echo "$RS_CONFIG" > /root/.riverscapes

cat<<EOF

    ▄   ███   ▄███▄     ▄▄▄▄▀   
     █  █  █  █▀   ▀ ▀▀▀ █      
█     █ █ ▀ ▄ ██▄▄       █      
 █    █ █  ▄▀ █▄   ▄▀   █       
  █  █  ███   ▀███▀    ▀        
   █▐                           
   ▐                            

EOF

echo "HUC: $HUC"
echo "PROGRAM: $PROGRAM"
echo "RSCONTEXT_TAGS: $RSCONTEXT_TAGS"
echo "VBET_TAGS: $VBET_TAGS"

# Drop into our venv immediately
source /usr/local/venv/bin/activate

# Install latest pip dependencies
pip --timeout=120 install -r /usr/local/requirements.txt


echo "======================  GDAL Version ======================="
gdal-config --version

# Define some folders that we can easily clean up later
TASK_DIR=/usr/local/data/vbet/$HUC
RS_CONTEXT_DIR=$TASK_DIR/rs_context
TASK_OUTPUT=$TASK_DIR/output

##########################################################################################
# First Get RS_Context inputs
##########################################################################################

# Get the RSCli project we need to make this happe-9
rscli download $RS_CONTEXT_DIR --type "RSContext" --meta "huc8=$HUC" \
  --file-filter "(hillshade|slope|dem|hand|hydrology)" \
  --tags "$RSCONTEXT_TAGS" --no-input --verbose --program "$PROGRAM"

##########################################################################################
# Now Run VBET
##########################################################################################
try() {

vbet $HUC \
  "KW_TESTING" \
  FLOWLINES=$RS_CONTEXT_DIR/hydrology/hydrology.gpkg/network,FLOW_AREA=$RS_CONTEXT_DIR/hydrology/NHDArea.shp,SLOPE_RASTER=$RS_CONTEXT_DIR/topography/slope.tif,DEM=$RS_CONTEXT_DIR/topography/dem.tif,HILLSHADE=$RS_CONTEXT_DIR/topography/dem_hillshade.tif,CATCHMENTS=$RS_CONTEXT_DIR/hydrology/NHDPlusCatchment.shp \
  $TASK_OUTPUT \
  $RS_CONTEXT_DIR/hydrology/nhd_data.sqlite/NHDPlusFlowlineVAA \
  --reach_codes 33400,46003,46006,46007,55800 \
  --create_centerline \
  --meta "Runner=Cybercastor" \
  --debug \
  --verbose
if [[ $? != 0 ]]; then return 1; fi

cd /usr/local/src/riverscapes-tools/packages/vbet
/usr/local/venv/bin/python -m vbet.vbet_rs \
  $TASK_OUTPUT/project.rs.xml \
  $RS_CONTEXT_DIR/project.rs.xml

echo "======================  Final Disk space usage ======================="
df -h

echo "======================  Upload to the warehouse ======================="

# Upload the HUC into the warehouse
cd $TASK_OUTPUT
rscli upload . --replace --tags "$VBET_TAGS" --no-input --verbose --program "$PROGRAM"
if [[ $? != 0 ]]; then return 1; fi

# Cleanup
cd /usr/local/
rm -fr $TASK_DIR

echo "<<PROCESS COMPLETE>>"


}
try || {
  # Emergency Cleanup
  cd /usr/local/
  echo "<<VBET PROCESS ENDED WITH AN ERROR>>"
  exit 1
}
