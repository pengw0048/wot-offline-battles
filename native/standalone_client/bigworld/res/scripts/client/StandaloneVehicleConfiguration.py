"""Use the original descriptor and server physics configuration producers.

The stock client cache contains the presentation-only xphysics projection.
The independent authority needs the original detailed configuration without
changing that shared client cache or replacing its component/crew calculations.
"""

import copy

import ResMgr
import nations
import physics_shared
from items import vehicles


def authorityDescriptor(client_descriptor):
    if isinstance(client_descriptor, vehicles.CompositeVehicleDescriptor):
        # The original composite forwards every setattr to BOTH modes. A
        # shallow copy followed by descriptor.type = ... would overwrite the
        # client's two cached types. Copy its explicit descriptor graph and
        # preserve the original active-mode field without invoking forwarding.
        descriptor = object.__new__(client_descriptor.__class__)
        descriptor.__dict__.update(client_descriptor.__dict__)
        descriptor.__dict__['_CompositeVehicleDescriptor__vehicleDescr'] = (
            authorityDescriptor(client_descriptor.defaultVehicleDescr))
        descriptor.__dict__['_CompositeVehicleDescriptor__siegeDescr'] = (
            authorityDescriptor(client_descriptor.siegeVehicleDescr))
        return descriptor
    vehicle_type = client_descriptor.type
    nation_id, unused_vehicle_id = vehicle_type.id
    vehicle_name = vehicle_type.name.split(':', 1)[1]
    resource = (vehicles._VEHICLE_TYPE_XML_PATH + nations.NAMES[nation_id] +
                '/' + vehicle_name +
                vehicles.VEHICLE_MODE_FILE_SUFFIX[vehicle_type.mode] + '.xml')
    section = ResMgr.openSection(resource)
    if section is None:
        raise RuntimeError('Original vehicle resource is unavailable: ' + resource)
    # Cache.__loadVehicle selects this exact resource, and VehicleType's
    # IS_CELLAPP branch calls this reader with the same context/subsection.
    detailed = vehicles._readXPhysics((None, resource), section, 'physics')
    if detailed is None or detailed.get('detailed') is None:
        raise RuntimeError('Original vehicle has no detailed physics: ' + resource)
    descriptor = copy.copy(client_descriptor)
    descriptor.type = copy.copy(vehicle_type)
    descriptor.type.xphysics = detailed
    return descriptor


def configure(physics, client_descriptor):
    descriptor = authorityDescriptor(client_descriptor)
    # Preserve the original default merging, module selection, material map,
    # gear ratios, gravity factor, hull/COM adjustments and native configure.
    physics_shared.initVehiclePhysicsServer(physics, descriptor)
    return descriptor
