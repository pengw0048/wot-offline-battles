# Included from the pinned BigWorld client/CMakeLists.txt after bwclient exists.
if(NOT TARGET bwclient)
    message(FATAL_ERROR "The standalone application must be linked into bwclient")
endif()

target_sources(bwclient PRIVATE "${CMAKE_CURRENT_LIST_DIR}/standalone_app.cpp")
target_include_directories(bwclient PRIVATE "${BW_SOURCE_DIR}/client")
