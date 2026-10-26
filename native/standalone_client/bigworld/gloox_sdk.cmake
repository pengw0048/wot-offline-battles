# Rebuild the official source independently; no original game C++ ABI is used.
file(TO_CMAKE_PATH "${WOT_GLOOX_SOURCE_ROOT}" WOT_GLOOX_SOURCE_ROOT)
if(NOT WIN32 OR NOT CMAKE_SIZEOF_VOID_P EQUAL 4)
    message(FATAL_ERROR "The standalone XMPP owner requires the Win32 target")
endif()
if(NOT EXISTS "${WOT_GLOOX_SOURCE_ROOT}/src/client.cpp" OR
   NOT EXISTS "${WOT_GLOOX_SOURCE_ROOT}/config.h.win")
    message(FATAL_ERROR "WOT_GLOOX_SOURCE_ROOT must contain official gloox 1.0.28 source")
endif()
file(READ "${WOT_GLOOX_SOURCE_ROOT}/src/gloox.cpp" WOT_GLOOX_VERSION_SOURCE)
if(NOT WOT_GLOOX_VERSION_SOURCE MATCHES "1\\.0\\.28")
    message(FATAL_ERROR "The reviewed XMPP source version is gloox 1.0.28")
endif()
# Match the official src/Makefile.am production list, excluding examples/tests.
set(WOT_GLOOX_SOURCE_NAMES
    jid.cpp
    parser.cpp
    connectiontcpclient.cpp
    clientbase.cpp
    tag.cpp
    stanza.cpp
    logsink.cpp
    dns.cpp
    prep.cpp
    base64.cpp
    client.cpp
    component.cpp
    disco.cpp
    adhoc.cpp
    privatexml.cpp
    registration.cpp
    nonsaslauth.cpp
    rosteritem.cpp
    rostermanager.cpp
    privacyitem.cpp
    bookmarkstorage.cpp
    annotations.cpp
    privacymanager.cpp
    lastactivity.cpp
    flexoff.cpp
    dataform.cpp
    dataformfield.cpp
    dataformfieldcontainer.cpp
    messagesession.cpp
    messageeventfilter.cpp
    chatstatefilter.cpp
    gloox.cpp
    inbandbytestream.cpp
    messagefilter.cpp
    vcard.cpp
    vcardmanager.cpp
    md5.cpp
    sha.cpp
    search.cpp
    dataformreported.cpp
    dataformitem.cpp
    mucroom.cpp
    mucmessagesession.cpp
    oob.cpp
    vcardupdate.cpp
    stanzaextensionfactory.cpp
    mucinvitationhandler.cpp
    delayeddelivery.cpp
    gpgencrypted.cpp
    gpgsigned.cpp
    uniquemucroom.cpp
    instantmucroom.cpp
    compressionzlib.cpp
    tlsgnutlsclient.cpp
    connectionhttpproxy.cpp
    tlsgnutlsserveranon.cpp
    tlsgnutlsbase.cpp
    tlsgnutlsclientanon.cpp
    tlsschannel.cpp
    tlsdefault.cpp
    simanager.cpp
    siprofileft.cpp
    mutex.cpp
    connectionsocks5proxy.cpp
    socks5bytestreammanager.cpp
    socks5bytestream.cpp
    connectiontcpbase.cpp
    connectiontcpserver.cpp
    socks5bytestreamserver.cpp
    amp.cpp
    pubsubitem.cpp
    pubsubmanager.cpp
    error.cpp
    util.cpp
    iq.cpp
    message.cpp
    presence.cpp
    subscription.cpp
    capabilities.cpp
    chatstate.cpp
    connectionbosh.cpp
    connectiontls.cpp
    messageevent.cpp
    receipt.cpp
    nickname.cpp
    eventdispatcher.cpp
    pubsubevent.cpp
    xhtmlim.cpp
    featureneg.cpp
    shim.cpp
    softwareversion.cpp
    attention.cpp
    tlsopensslclient.cpp
    tlsopensslbase.cpp
    tlsopensslserver.cpp
    compressiondefault.cpp
    connectiontlsserver.cpp
    atomicrefcount.cpp
    linklocalmanager.cpp
    linklocalclient.cpp
    forward.cpp
    jinglesession.cpp
    jinglecontent.cpp
    jinglesessionmanager.cpp
    carbons.cpp
    jinglepluginfactory.cpp
    jingleiceudp.cpp
    jinglefiletransfer.cpp
    iodata.cpp
)
set(WOT_GLOOX_SOURCES)
foreach(name IN LISTS WOT_GLOOX_SOURCE_NAMES)
    list(APPEND WOT_GLOOX_SOURCES "${WOT_GLOOX_SOURCE_ROOT}/src/${name}")
endforeach()
add_library(WotGloox STATIC ${WOT_GLOOX_SOURCES})
target_include_directories(WotGloox PUBLIC "${WOT_GLOOX_SOURCE_ROOT}/src")
# Upstream config.h.win selects SChannel. Keep optional external TLS/IDN/zlib
# macros unset, and keep the same dynamic MSVC runtime as the public engine.
target_link_libraries(WotGloox PUBLIC ws2_32 dnsapi secur32 crypt32 gdi32)
set_target_properties(WotGloox PROPERTIES CXX_STANDARD 17 CXX_STANDARD_REQUIRED YES)
if(MSVC)
    # Upstream warning policy stays local to this independently built library.
    target_compile_options(WotGloox PRIVATE /W3 /WX-)
endif()
