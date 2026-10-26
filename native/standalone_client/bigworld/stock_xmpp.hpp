#pragma once
#if defined(WOT_XMPP_HOST_BUILD)
namespace BW {
#else
#include "cstdmf/bw_namespace.hpp"
BW_BEGIN_NAMESPACE
#endif

// Register before the original messenger modules are imported. Construction
// remains inert; only an explicit connect request creates a transport worker.
bool initialiseStockXmpp();
void tickStockXmpp();
void finiStockXmpp();

#if defined(WOT_XMPP_HOST_BUILD)
}
#else
BW_END_NAMESPACE
#endif
