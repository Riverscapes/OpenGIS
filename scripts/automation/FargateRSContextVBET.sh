#!/bin/bash
set -eu
IFS=$'\n\t'

# These environment variables need to be present before the script starts
(: "${HUC?}")
(: "${PROGRAM?}")
(: "${RS_CONFIG?}")
(: "${TAGS?}")

echo "$RS_CONFIG" > /root/.riverscapes

cat<<EOF
      ██████╗ ███████╗ ██████╗ ██████╗ ███╗   ██╗████████╗███████╗██╗  ██╗████████╗  
      ██╔══██╗██╔════╝██╔════╝██╔═══██╗████╗  ██║╚══██╔══╝██╔════╝╚██╗██╔╝╚══██╔══╝  
      ██████╔╝███████╗██║     ██║   ██║██╔██╗ ██║   ██║   █████╗   ╚███╔╝    ██║     
      ██╔══██╗╚════██║██║     ██║   ██║██║╚██╗██║   ██║   ██╔══╝   ██╔██╗    ██║     
      ██║  ██║███████║╚██████╗╚██████╔╝██║ ╚████║   ██║   ███████╗██╔╝ ██╗   ██║     
      ╚═╝  ╚═╝╚══════╝ ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝   ╚═╝   ╚══════╝╚═╝  ╚═╝   ╚═╝     
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
echo "TAGS: $TAGS"

# Drop into our venv immediately
source /usr/local/venv/bin/activate

echo "======================  GDAL Version ======================="
gdal-config --version

# Define some folders that we can easily clean up later
RSC_TASK_DIR=/usr/local/data/rs_context/$HUC
RSC_TASK_OUTPUT=$RSC_TASK_DIR/output
RSC_TASK_DOWNLOAD=$RSC_TASK_DIR/scratch

VBET_TASK_DIR=/usr/local/data/vbet/$HUC
VBET_TASK_OUTPUT=$VBET_TASK_DIR/output

##########################################################################################
# First Run RS_Context
##########################################################################################

try() {

rscontext $HUC \
  /shared/NationalDatasets/landfire/200/us_200evt.tif \
  /shared/NationalDatasets/landfire/200/us_200bps.tif \
  /shared/NationalDatasets/ownership/surface_management_agency.shp \
  /shared/NationalDatasets/ownership/FairMarketValue.tif \
  /shared/NationalDatasets/ecoregions/us_eco_l3_state_boundaries.shp \
  /shared/download/prism \
  $RSC_TASK_OUTPUT \
  /shared/download/ \
  --parallel \
  --temp_folder $RSC_TASK_DOWNLOAD \
  --meta "Runner=Cybercastor" \
  --verbose

if [[ $? != 0 ]]; then return 1; fi
echo "<<RS_CONTEXT COMPLETE>>"

# Upload the HUC into the warehouse
cd $RSC_TASK_OUTPUT
rscli upload . --replace --tags "$TAGS"  --no-input --verbose --program "$PROGRAM"
if [[ $? != 0 ]]; then return 1; fi
echo "<<RS_CONTEXT UPLOAD COMPLETE>>"

# We need to conserver some space for the next run
rm -fr $RSC_TASK_OUTPUT/vegetation
rm -fr $RSC_TASK_OUTPUT/transportation
rm -fr $RSC_TASK_OUTPUT/climate

##########################################################################################
# Now Run VBET
##########################################################################################

vbet $HUC \
  "KW_TESTING" \
  FLOWLINES=$RSC_TASK_OUTPUT/hydrology/hydrology.gpkg/network,FLOW_AREA=$RSC_TASK_OUTPUT/hydrology/NHDArea.shp,SLOPE_RASTER=$RSC_TASK_OUTPUT/topography/slope.tif,DEM=$RSC_TASK_OUTPUT/topography/dem.tif,HILLSHADE=$RSC_TASK_OUTPUT/topography/dem_hillshade.tif,CATCHMENTS=$RSC_TASK_OUTPUT/hydrology/NHDPlusCatchment.shp \
  $VBET_TASK_OUTPUT \
  $RSC_TASK_OUTPUT/hydrology/nhd_data.sqlite/NHDPlusFlowlineVAA \
  --reach_codes 33400,46003,46006,46007,55800 \
  --create_centerline \
  --meta "Runner=Cybercastor" \
  --verbose
if [[ $? != 0 ]]; then return 1; fi

cd /usr/local/src/riverscapes-tools/packages/vbet
/usr/local/venv/bin/python -m vbet.vbet_rs \
  $VBET_TASK_OUTPUT/project.rs.xml \
  $RSC_TASK_OUTPUT/project.rs.xml


echo "<<VBET COMPLETE>>"

echo "======================  Final Disk space usage ======================="
df -h

echo "======================  Upload to the warehouse ======================="

# Upload the HUC into the warehouse
cd $VBET_TASK_OUTPUT
rscli upload . --replace --tags "$TAGS" --no-input --verbose --program "$PROGRAM"
if [[ $? != 0 ]]; then return 1; fi
echo "<<VBET UPLOAD COMPLETE>>"

# Cleanup
cd /usr/local/
rm -fr $RSC_TASK_DIR
rm -fr $VBET_TASK_DIR
echo "<<PROCESS COMPLETE>>"

}
try || {
  # Emergency Cleanup
  cd /usr/local/
  rm -fr $RSC_TASK_DIR
  rm -fr $VBET_TASK_DIR
  echo "<<RS CONTEXT PROCESS ENDED WITH AN ERROR>>"
  exit 1
}
