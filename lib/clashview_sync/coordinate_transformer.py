# -*- coding: utf-8 -*-
"""Coordinate transformations, vector/quaternion math, and unit conversion for ClashView Sync.

Provides mathematical foundations for transforming coordinates between:
- Navisworks native coordinates (arbitrary units: m, mm, cm, in, ft, etc.)
- Revit internal coordinates (always decimal feet)
- Revit shared coordinates (survey / project coordinate system)

Pure Python implementation: independently testable offline without Revit API assemblies.
Compatible with Python 2.7 (pyRevit IronPython) and Python 3.x.
"""
from __future__ import absolute_import, division, print_function

import math

# Exact international definition: 1 foot = 0.3048 meters
_METER_TO_FEET = 1.0 / 0.3048

# Complete unit conversion factors into decimal feet (Revit internal length unit)
UNITS_TO_FEET = {
    # Metric
    "m": _METER_TO_FEET,
    "meter": _METER_TO_FEET,
    "meters": _METER_TO_FEET,
    "metre": _METER_TO_FEET,
    "metres": _METER_TO_FEET,
    "cm": 0.01 * _METER_TO_FEET,
    "centimeter": 0.01 * _METER_TO_FEET,
    "centimeters": 0.01 * _METER_TO_FEET,
    "centimetre": 0.01 * _METER_TO_FEET,
    "centimetres": 0.01 * _METER_TO_FEET,
    "mm": 0.001 * _METER_TO_FEET,
    "millimeter": 0.001 * _METER_TO_FEET,
    "millimeters": 0.001 * _METER_TO_FEET,
    "millimetre": 0.001 * _METER_TO_FEET,
    "millimetres": 0.001 * _METER_TO_FEET,
    "km": 1000.0 * _METER_TO_FEET,
    "kilometer": 1000.0 * _METER_TO_FEET,
    "kilometers": 1000.0 * _METER_TO_FEET,
    "um": 0.000001 * _METER_TO_FEET,
    "micrometer": 0.000001 * _METER_TO_FEET,
    "micrometers": 0.000001 * _METER_TO_FEET,
    # Imperial
    "ft": 1.0,
    "feet": 1.0,
    "foot": 1.0,
    "in": 1.0 / 12.0,
    "inch": 1.0 / 12.0,
    "inches": 1.0 / 12.0,
    "yrd": 3.0,
    "yard": 3.0,
    "yards": 3.0,
    "mi": 5280.0,
    "mile": 5280.0,
    "miles": 5280.0,
    "mils": 0.001 / 12.0,
    "uin": 0.000001 / 12.0,
}

# Coordinate Space Identifiers
COORD_SPACE_NAVISWORKS_NATIVE = "NAVISWORKS_NATIVE"
COORD_SPACE_NAVISWORKS_FEET = "NAVISWORKS_FEET"
COORD_SPACE_REVIT_INTERNAL = "REVIT_INTERNAL"
COORD_SPACE_REVIT_SHARED = "REVIT_SHARED"


class CoordinateFrameStatus(object):
    """Status of coordinate registration between Navisworks and Revit."""

    VERIFIED_INTERNAL = "VERIFIED_INTERNAL"
    VERIFIED_SHARED = "VERIFIED_SHARED"
    UNVERIFIED_COORDINATE_FRAME = "UNVERIFIED_COORDINATE_FRAME"


class FramingMethod(object):
    """Method used to compute 3D section box and camera framing."""

    EXACT_CAMERA_SYNC = "EXACT_CAMERA_SYNC"
    AUTO_FRAMED_PARTICIPANTS = "AUTO_FRAMED_PARTICIPANTS"
    SINGLE_PARTICIPANT_FALLBACK = "SINGLE_PARTICIPANT_FALLBACK"
    UNAVAILABLE_GEOMETRY_FALLBACK = "UNAVAILABLE_GEOMETRY_FALLBACK"


class IsolationMethod(object):
    """Method used to isolate or contextualize clash participants in a Revit 3D view."""

    NATIVE_ELEMENT_ISOLATION = "NATIVE_ELEMENT_ISOLATION"
    LINK_CATEGORY_AND_SECTION_BOX = "LINK_CATEGORY_AND_SECTION_BOX"
    CONTEXT_SECTION_BOX_ONLY = "CONTEXT_SECTION_BOX_ONLY"
    NONE = "NONE"


def get_feet_scale_factor(from_unit):
    """Returns the multiplication factor to convert from_unit into decimal feet.

    Raises ValueError if from_unit is not recognized.
    """
    if not from_unit:
        return _METER_TO_FEET  # default to meters if unspecified
    clean_unit = str(from_unit).strip().lower()
    if clean_unit not in UNITS_TO_FEET:
        raise ValueError("Unsupported or unknown unit: '{0}'".format(from_unit))
    return UNITS_TO_FEET[clean_unit]


def convert_length(value, from_unit, to_unit="ft"):
    """Converts a scalar length value from from_unit into to_unit."""
    scale_from = get_feet_scale_factor(from_unit)
    scale_to = get_feet_scale_factor(to_unit)
    # Convert to feet first, then to target unit
    value_in_feet = float(value) * scale_from
    return value_in_feet / scale_to


class Vector3(object):
    """Lightweight 3D vector supporting vector algebra and normalization."""

    __slots__ = ("x", "y", "z")

    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __add__(self, other):
        return Vector3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other):
        return Vector3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, scalar):
        return Vector3(self.x * scalar, self.y * scalar, self.z * scalar)

    __rmul__ = __mul__

    def __truediv__(self, scalar):
        s = float(scalar)
        if abs(s) < 1e-12:
            raise ZeroDivisionError("Cannot divide Vector3 by near-zero scalar.")
        return Vector3(self.x / s, self.y / s, self.z / s)

    __div__ = __truediv__

    def __neg__(self):
        return Vector3(-self.x, -self.y, -self.z)

    def dot(self, other):
        """Calculates scalar dot product with other Vector3."""
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other):
        """Calculates vector cross product (self x other)."""
        return Vector3(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )

    def length_squared(self):
        """Returns squared length (norm) of the vector."""
        return self.x * self.x + self.y * self.y + self.z * self.z

    def length(self):
        """Returns Euclidean length (norm) of the vector."""
        return math.sqrt(self.length_squared())

    def normalize(self, tol=1e-12):
        """Returns a new normalized unit Vector3.

        Raises ValueError if vector length is close to zero.
        """
        mag = self.length()
        if mag < tol:
            raise ValueError(
                "Cannot normalize near-zero Vector3: length is {0:.3e}".format(mag)
            )
        return Vector3(self.x / mag, self.y / mag, self.z / mag)

    def is_almost_equal(self, other, tol=1e-6):
        """Checks component-wise equality within tolerance."""
        if not isinstance(other, Vector3):
            return False
        return (
            abs(self.x - other.x) < tol
            and abs(self.y - other.y) < tol
            and abs(self.z - other.z) < tol
        )

    def to_tuple(self):
        """Returns components as a tuple."""
        return (self.x, self.y, self.z)

    @property
    def X(self):
        """Revit XYZ compatibility alias for x."""
        return self.x

    @property
    def Y(self):
        """Revit XYZ compatibility alias for y."""
        return self.y

    @property
    def Z(self):
        """Revit XYZ compatibility alias for z."""
        return self.z

    def to_dict(self):
        """Serializes components to dictionary."""
        return {"x": self.x, "y": self.y, "z": self.z}

    def __repr__(self):
        return "Vector3({0:.6f}, {1:.6f}, {2:.6f})".format(self.x, self.y, self.z)

    def __eq__(self, other):
        if not isinstance(other, Vector3):
            return False
        return self.is_almost_equal(other, tol=1e-7)


def _extract_xyz(pt):
    """Extracts (x, y, z) floats from Vector3, ClashPoint, Revit XYZ, or sequence."""
    if isinstance(pt, Vector3):
        return (pt.x, pt.y, pt.z)
    if hasattr(pt, "x") and hasattr(pt, "y") and hasattr(pt, "z"):
        return (float(pt.x), float(pt.y), float(pt.z))
    if hasattr(pt, "X") and hasattr(pt, "Y") and hasattr(pt, "Z"):
        return (float(pt.X), float(pt.Y), float(pt.Z))
    if isinstance(pt, (tuple, list)) and len(pt) >= 3:
        return (float(pt[0]), float(pt[1]), float(pt[2]))
    raise TypeError(
        "Expected Vector3, ClashPoint, Revit XYZ, or 3-tuple/list; got {0}".format(
            type(pt)
        )
    )


def _to_vector3(pt):
    """Converts a point-like object into a Vector3 instance."""
    if isinstance(pt, Vector3):
        return pt
    x, y, z = _extract_xyz(pt)
    return Vector3(x, y, z)


class BoundingBox3D(object):
    """Axis-aligned 3D bounding box supporting 8-corner transforms, expansion, and queries."""

    def __init__(self, min_pt, max_pt, coord_space=COORD_SPACE_REVIT_INTERNAL):
        v_min = _to_vector3(min_pt)
        v_max = _to_vector3(max_pt)

        if v_min.x > v_max.x or v_min.y > v_max.y or v_min.z > v_max.z:
            raise ValueError(
                "Inverted BoundingBox3D coordinates: min_pt {0} exceeds max_pt {1}.".format(
                    v_min, v_max
                )
            )

        self.min_pt = v_min
        self.max_pt = v_max
        self.coord_space = coord_space or COORD_SPACE_REVIT_INTERNAL

    @property
    def Min(self):
        """Revit BoundingBoxXYZ compatibility alias for min_pt."""
        return self.min_pt

    @property
    def Max(self):
        """Revit BoundingBoxXYZ compatibility alias for max_pt."""
        return self.max_pt

    @property
    def center(self):
        """Returns the geometric center Vector3 of the bounding box."""
        return Vector3(
            0.5 * (self.min_pt.x + self.max_pt.x),
            0.5 * (self.min_pt.y + self.max_pt.y),
            0.5 * (self.min_pt.z + self.max_pt.z),
        )

    @property
    def size(self):
        """Returns the box dimensions (dx, dy, dz) as a Vector3."""
        return Vector3(
            self.max_pt.x - self.min_pt.x,
            self.max_pt.y - self.min_pt.y,
            self.max_pt.z - self.min_pt.z,
        )

    @property
    def volume(self):
        """Returns the scalar volume (dx * dy * dz) of the bounding box."""
        s = self.size
        return s.x * s.y * s.z

    def is_degenerate(self, tol=1e-6):
        """Returns True if any axis span of the bounding box is <= tol."""
        s = self.size
        return s.x <= tol or s.y <= tol or s.z <= tol

    def corners(self):
        """Returns all 8 corner Vector3 points of the bounding box."""
        x0, y0, z0 = self.min_pt.x, self.min_pt.y, self.min_pt.z
        x1, y1, z1 = self.max_pt.x, self.max_pt.y, self.max_pt.z
        return [
            Vector3(x0, y0, z0),
            Vector3(x1, y0, z0),
            Vector3(x0, y1, z0),
            Vector3(x1, y1, z0),
            Vector3(x0, y0, z1),
            Vector3(x1, y0, z1),
            Vector3(x0, y1, z1),
            Vector3(x1, y1, z1),
        ]

    def expand(self, margin_ft):
        """Returns a new BoundingBox3D expanded outward by margin_ft on all 6 faces."""
        m = float(margin_ft)
        offset = Vector3(m, m, m)
        return BoundingBox3D(
            self.min_pt - offset,
            self.max_pt + offset,
            coord_space=self.coord_space,
        )

    def enforce_min_dimensions(self, min_span_ft=4.0):
        """Returns a new BoundingBox3D centered on self.center with each axis >= min_span_ft."""
        min_span = float(min_span_ft)
        if min_span <= 0.0:
            return BoundingBox3D(self.min_pt, self.max_pt, coord_space=self.coord_space)

        c = self.center
        s = self.size
        hx = 0.5 * max(s.x, min_span)
        hy = 0.5 * max(s.y, min_span)
        hz = 0.5 * max(s.z, min_span)

        half_vec = Vector3(hx, hy, hz)
        return BoundingBox3D(
            c - half_vec,
            c + half_vec,
            coord_space=self.coord_space,
        )

    def union(self, other_box):
        """Returns the smallest BoundingBox3D enclosing both self and other_box."""
        if not isinstance(other_box, BoundingBox3D):
            raise TypeError("Expected BoundingBox3D for union.")
        return BoundingBox3D(
            Vector3(
                min(self.min_pt.x, other_box.min_pt.x),
                min(self.min_pt.y, other_box.min_pt.y),
                min(self.min_pt.z, other_box.min_pt.z),
            ),
            Vector3(
                max(self.max_pt.x, other_box.max_pt.x),
                max(self.max_pt.y, other_box.max_pt.y),
                max(self.max_pt.z, other_box.max_pt.z),
            ),
            coord_space=self.coord_space,
        )

    def intersection(self, other_box):
        """Returns the BoundingBox3D representing the 3D overlap of self and other_box, or None if disjoint."""
        if not isinstance(other_box, BoundingBox3D):
            raise TypeError("Expected BoundingBox3D for intersection.")
        min_x = max(self.min_pt.x, other_box.min_pt.x)
        min_y = max(self.min_pt.y, other_box.min_pt.y)
        min_z = max(self.min_pt.z, other_box.min_pt.z)
        max_x = min(self.max_pt.x, other_box.max_pt.x)
        max_y = min(self.max_pt.y, other_box.max_pt.y)
        max_z = min(self.max_pt.z, other_box.max_pt.z)
        if min_x > max_x or min_y > max_y or min_z > max_z:
            return None
        return BoundingBox3D(
            Vector3(min_x, min_y, min_z),
            Vector3(max_x, max_y, max_z),
            coord_space=self.coord_space,
        )

    def intersects(self, other_box, tolerance_ft=0.0):
        """Returns True if self and other_box overlap within tolerance_ft on all 3 axes."""
        if not isinstance(other_box, BoundingBox3D):
            raise TypeError("Expected BoundingBox3D for intersects.")
        tol = float(tolerance_ft)
        return (
            (self.min_pt.x - tol <= other_box.max_pt.x and self.max_pt.x + tol >= other_box.min_pt.x)
            and (self.min_pt.y - tol <= other_box.max_pt.y and self.max_pt.y + tol >= other_box.min_pt.y)
            and (self.min_pt.z - tol <= other_box.max_pt.z and self.max_pt.z + tol >= other_box.min_pt.z)
        )

    def min_distance_to(self, other_box):
        """Returns the minimum Euclidean distance between self and other_box (0.0 if intersecting)."""
        if not isinstance(other_box, BoundingBox3D):
            raise TypeError("Expected BoundingBox3D for min_distance_to.")
        dx = max(0.0, other_box.min_pt.x - self.max_pt.x, self.min_pt.x - other_box.max_pt.x)
        dy = max(0.0, other_box.min_pt.y - self.max_pt.y, self.min_pt.y - other_box.max_pt.y)
        dz = max(0.0, other_box.min_pt.z - self.max_pt.z, self.min_pt.z - other_box.max_pt.z)
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def contains_point(self, pt, tolerance_ft=0.0):
        """Returns True if point pt lies inside the bounding box within tolerance_ft."""
        v = _to_vector3(pt)
        tol = float(tolerance_ft)
        return (
            (self.min_pt.x - tol <= v.x <= self.max_pt.x + tol)
            and (self.min_pt.y - tol <= v.y <= self.max_pt.y + tol)
            and (self.min_pt.z - tol <= v.z <= self.max_pt.z + tol)
        )

    def to_dict(self):
        """Serializes bounding box data to a dictionary."""
        return {
            "min_pt": self.min_pt.to_dict(),
            "max_pt": self.max_pt.to_dict(),
            "center": self.center.to_dict(),
            "size": self.size.to_dict(),
            "volume": self.volume,
            "coord_space": self.coord_space,
        }

    def __repr__(self):
        return "BoundingBox3D(min={0}, max={1}, space='{2}')".format(
            self.min_pt, self.max_pt, self.coord_space
        )


class Quaternion(object):
    """Represents a rotation quaternion (a, b, c, d) in Navisworks convention.

    In Navisworks XML:
      a = q_x (imaginary component)
      b = q_y (imaginary component)
      c = q_z (imaginary component)
      d = q_w (scalar real component)
    """

    __slots__ = ("a", "b", "c", "d")

    def __init__(self, a=0.0, b=0.0, c=0.0, d=1.0):
        self.a = float(a)
        self.b = float(b)
        self.c = float(c)
        self.d = float(d)

    def norm(self):
        """Calculates Euclidean norm of the quaternion."""
        return math.sqrt(
            self.a * self.a + self.b * self.b + self.c * self.c + self.d * self.d
        )

    def is_normalized(self, tol=1e-5):
        """Checks whether quaternion norm is within tolerance of 1.0."""
        return abs(self.norm() - 1.0) < tol

    def normalize(self, tol=1e-12):
        """Returns a normalized unit Quaternion."""
        n = self.norm()
        if n < tol:
            raise ValueError(
                "Cannot normalize near-zero Quaternion: norm is {0:.3e}".format(n)
            )
        return Quaternion(self.a / n, self.b / n, self.c / n, self.d / n)

    def to_camera_vectors(self):
        """Computes camera forward, up, and right vectors from quaternion rotation.

        In Navisworks default unrotated camera frame:
          Forward = (0, 0, -1)   [looking down -Z]
          Up      = (0, 1, 0)    [up along +Y]
          Right   = (1, 0, 0)    [right along +X]

        Applying the rotation matrix R(q) yields:
          Forward = Vector3(-2*(a*c + b*d), -2*(b*c - a*d), -(1 - 2*(a^2 + b^2)))
          Up      = Vector3(2*(a*b - c*d), 1 - 2*(a^2 + c^2), 2*(b*c + a*d))
          Right   = Vector3(1 - 2*(b^2 + c^2), 2*(a*b + c*d), 2*(a*c - b*d))

        Returns tuple: (forward_vector, up_vector, right_vector)
        """
        # Ensure quaternion is normalized before generating orthogonal vectors
        q = self.normalize() if not self.is_normalized() else self
        a, b, c, d = q.a, q.b, q.c, q.d

        a2 = a * a
        b2 = b * b
        c2 = c * c

        # Rotated forward (default 0, 0, -1)
        f_x = -2.0 * (a * c + b * d)
        f_y = -2.0 * (b * c - a * d)
        f_z = -(1.0 - 2.0 * (a2 + b2))
        forward = Vector3(f_x, f_y, f_z).normalize()

        # Rotated up (default 0, 1, 0)
        u_x = 2.0 * (a * b - c * d)
        u_y = 1.0 - 2.0 * (a2 + c2)
        u_z = 2.0 * (b * c + a * d)
        up = Vector3(u_x, u_y, u_z).normalize()

        # Rotated right (default 1, 0, 0)
        r_x = 1.0 - 2.0 * (b2 + c2)
        r_y = 2.0 * (a * b + c * d)
        r_z = 2.0 * (a * c - b * d)
        right = Vector3(r_x, r_y, r_z).normalize()

        return (forward, up, right)

    def to_rotation_matrix(self):
        """Returns 3x3 rotation matrix as a tuple of 3 rows."""
        q = self.normalize() if not self.is_normalized() else self
        a, b, c, d = q.a, q.b, q.c, q.d

        a2, b2, c2 = a * a, b * b, c * c

        r00 = 1.0 - 2.0 * (b2 + c2)
        r01 = 2.0 * (a * b - c * d)
        r02 = 2.0 * (a * c + b * d)

        r10 = 2.0 * (a * b + c * d)
        r11 = 1.0 - 2.0 * (a2 + c2)
        r12 = 2.0 * (b * c - a * d)

        r20 = 2.0 * (a * c - b * d)
        r21 = 2.0 * (b * c + a * d)
        r22 = 1.0 - 2.0 * (a2 + b2)

        return (
            (r00, r01, r02),
            (r10, r11, r12),
            (r20, r21, r22),
        )

    def to_tuple(self):
        """Returns quaternion as an (a, b, c, d) tuple."""
        return (self.a, self.b, self.c, self.d)

    def __repr__(self):
        return "Quaternion(a={0:.6f}, b={1:.6f}, c={2:.6f}, d={3:.6f})".format(
            self.a, self.b, self.c, self.d
        )


def validate_and_orthogonalize_camera(forward, up, tol=1e-4):
    """Validates camera vectors for orthogonality and unit length.

    Revit's ViewOrientation3D constructor requires:
      - forward and up must be non-zero
      - forward and up must be perpendicular: dot(forward, up) == 0

    If dot(forward, up) is non-zero, this function applies Gram-Schmidt
    orthogonalization to adjust 'up' without altering the line of sight (forward).

    Returns:
      (forward_norm, up_ortho, right_norm, was_adjusted)
    """
    f_norm = forward.normalize()
    u_norm = up.normalize()

    dot_val = f_norm.dot(u_norm)
    was_adjusted = False

    if abs(dot_val) > tol:
        # Gram-Schmidt projection: up_ortho = up - (up . forward) * forward
        proj = f_norm * dot_val
        u_adjusted = (u_norm - proj).normalize()
        was_adjusted = True
    else:
        u_adjusted = u_norm

    # Right-handed camera right vector
    r_norm = f_norm.cross(u_adjusted).normalize()

    return (f_norm, u_adjusted, r_norm, was_adjusted)


class CoordinateTransform(object):
    """Represents a rigid coordinate frame transformation: p' = R * (scale * p) + T."""

    def __init__(
        self,
        source_space=COORD_SPACE_NAVISWORKS_NATIVE,
        target_space=COORD_SPACE_REVIT_INTERNAL,
        scale=1.0,
        rotation_matrix=None,
        translation=None,
        description=None,
    ):
        self.source_space = source_space
        self.target_space = target_space
        self.scale = float(scale)

        # 3x3 rotation matrix (tuple of 3 rows or None for identity)
        if rotation_matrix:
            self.rotation_matrix = rotation_matrix
        else:
            self.rotation_matrix = (
                (1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
                (0.0, 0.0, 1.0),
            )

        # Translation vector (Vector3 or None for origin)
        if isinstance(translation, Vector3):
            self.translation = translation
        elif isinstance(translation, (tuple, list)) and len(translation) >= 3:
            self.translation = Vector3(translation[0], translation[1], translation[2])
        else:
            self.translation = Vector3(0.0, 0.0, 0.0)

        self.description = description or "{0} -> {1}".format(
            source_space, target_space
        )

    def is_identity(self, tol=1e-6):
        """Checks if transform is identity (scale=1, R=I, T=0)."""
        if abs(self.scale - 1.0) > tol:
            return False
        if self.translation.length() > tol:
            return False
        m = self.rotation_matrix
        identity = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        for r in range(3):
            for c in range(3):
                if abs(m[r][c] - identity[r][c]) > tol:
                    return False
        return True

    def transform_point(self, point):
        """Transforms a Vector3 or tuple from source to target space."""
        if hasattr(point, "x") and hasattr(point, "y") and hasattr(point, "z"):
            px, py, pz = point.x, point.y, point.z
        elif isinstance(point, (tuple, list)) and len(point) >= 3:
            px, py, pz = point[0], point[1], point[2]
        else:
            raise TypeError("Expected Vector3, ClashPoint, or (x, y, z) tuple.")

        # 1. Scale
        sx = px * self.scale
        sy = py * self.scale
        sz = pz * self.scale

        # 2. Rotate R * (s * p)
        m = self.rotation_matrix
        rx = m[0][0] * sx + m[0][1] * sy + m[0][2] * sz
        ry = m[1][0] * sx + m[1][1] * sy + m[1][2] * sz
        rz = m[2][0] * sx + m[2][1] * sy + m[2][2] * sz

        # 3. Translate
        tx = rx + self.translation.x
        ty = ry + self.translation.y
        tz = rz + self.translation.z

        return Vector3(tx, ty, tz)

    def transform_vector(self, vec):
        """Transforms a direction/normal Vector3 or tuple using rotation only (no scale or translation)."""
        vx, vy, vz = _extract_xyz(vec)
        m = self.rotation_matrix
        rx = m[0][0] * vx + m[0][1] * vy + m[0][2] * vz
        ry = m[1][0] * vx + m[1][1] * vy + m[1][2] * vz
        rz = m[2][0] * vx + m[2][1] * vy + m[2][2] * vz
        return Vector3(rx, ry, rz)

    def transform_bounding_box(self, bbox_or_min, max_pt=None):
        """Transforms an axis-aligned bounding box by transforming all 8 corners.

        Transforming only min and max points fails under rotations (e.g. 90/180/45 deg)
        and reflections/mirrors. This method transforms all 8 corners and computes the
        new axis-aligned bounding box in target_space.
        """
        if isinstance(bbox_or_min, BoundingBox3D) and max_pt is None:
            box = bbox_or_min
        else:
            box = BoundingBox3D(bbox_or_min, max_pt, coord_space=self.source_space)

        transformed_corners = [self.transform_point(c) for c in box.corners()]
        min_x = min(p.x for p in transformed_corners)
        min_y = min(p.y for p in transformed_corners)
        min_z = min(p.z for p in transformed_corners)
        max_x = max(p.x for p in transformed_corners)
        max_y = max(p.y for p in transformed_corners)
        max_z = max(p.z for p in transformed_corners)

        return BoundingBox3D(
            Vector3(min_x, min_y, min_z),
            Vector3(max_x, max_y, max_z),
            coord_space=self.target_space,
        )

    def inverse(self):
        """Calculates inverse transformation."""
        inv_scale = 1.0 / self.scale if abs(self.scale) > 1e-12 else 1.0

        # Transpose of rotation matrix R^T
        m = self.rotation_matrix
        inv_r = (
            (m[0][0], m[1][0], m[2][0]),
            (m[0][1], m[1][1], m[2][1]),
            (m[0][2], m[1][2], m[2][2]),
        )

        # Inverse translation: - R^T * T / scale
        t = self.translation
        it_x = -(inv_r[0][0] * t.x + inv_r[0][1] * t.y + inv_r[0][2] * t.z) * inv_scale
        it_y = -(inv_r[1][0] * t.x + inv_r[1][1] * t.y + inv_r[1][2] * t.z) * inv_scale
        it_z = -(inv_r[2][0] * t.x + inv_r[2][1] * t.y + inv_r[2][2] * t.z) * inv_scale

        return CoordinateTransform(
            source_space=self.target_space,
            target_space=self.source_space,
            scale=inv_scale,
            rotation_matrix=inv_r,
            translation=Vector3(it_x, it_y, it_z),
            description="Inverse of ({0})".format(self.description),
        )

    def __repr__(self):
        return "CoordinateTransform({0}, scale={1:.4f}, T={2})".format(
            self.description, self.scale, self.translation
        )


def calculate_fallback_framing(clash_point_ft, margin_ft=10.0, direction=None):
    """Calculates camera eye, forward, up, and section box bounds for a clash point in feet.

    Used when no saved viewpoint is available in the Navisworks export.

    Args:
      clash_point_ft: Vector3 or (x, y, z) representing clash center in Revit feet.
      margin_ft: clearance distance around the clash point in feet (default 10 ft).
      direction: optional custom line of sight Vector3. Default is standard isometric.

    Returns:
      dict with:
        'eye_position': Vector3
        'forward_vector': Vector3
        'up_vector': Vector3
        'box_min': Vector3
        'box_max': Vector3
    """
    if not isinstance(clash_point_ft, Vector3):
        pt = Vector3(clash_point_ft[0], clash_point_ft[1], clash_point_ft[2])
    else:
        pt = clash_point_ft

    margin = max(float(margin_ft), 1.0)

    # Standard isometric view direction looking from (+X, -Y, +Z) towards center
    if direction:
        forward = direction.normalize()
    else:
        # Looking down from South-East towards North-West
        forward = Vector3(-1.0, 1.0, -0.8).normalize()

    # Up vector orthogonal to forward and biased towards +Z
    world_z = Vector3(0.0, 0.0, 1.0)
    dot_z = forward.dot(world_z)
    up_raw = world_z - forward * dot_z
    if up_raw.length_squared() < 1e-4:
        # Forward is nearly vertical; use +Y as up
        up_raw = Vector3(0.0, 1.0, 0.0)
    up = up_raw.normalize()

    # Place camera eye back along the reverse forward direction
    eye_distance = margin * 3.5
    eye = pt - forward * eye_distance

    # Section box bounds centered on clash point with specified margin
    box_min = pt - Vector3(margin, margin, margin)
    box_max = pt + Vector3(margin, margin, margin)

    return {
        "eye_position": eye,
        "forward_vector": forward,
        "up_vector": up,
        "box_min": box_min,
        "box_max": box_max,
    }


def _apply_transform_to_points(points, xform):
    """Applies a CoordinateTransform, Revit DB.Transform, or duck-typed Transform to 3D points."""
    if xform is None:
        return list(points)

    # Check explicit IsIdentity flag if boolean True
    is_ident = getattr(xform, "IsIdentity", None)
    if is_ident is True:
        return list(points)
    if hasattr(xform, "is_identity") and callable(xform.is_identity):
        if xform.is_identity():
            return list(points)

    # 1. CoordinateTransform or object with transform_point
    if hasattr(xform, "transform_point") and callable(xform.transform_point):
        return [_to_vector3(xform.transform_point(p)) for p in points]

    # 2. Object with callable OfPoint (e.g. Revit Transform or mock Transform)
    if hasattr(xform, "OfPoint") and callable(xform.OfPoint):
        try:
            return [_to_vector3(xform.OfPoint(p)) for p in points]
        except Exception:
            try:
                import Autodesk.Revit.DB as _DB
                return [
                    _to_vector3(xform.OfPoint(_DB.XYZ(p.x, p.y, p.z)))
                    for p in points
                ]
            except Exception:
                pass

    # 3. BasisX / BasisY / BasisZ / Origin decomposition (Revit Transform structure)
    if (
        hasattr(xform, "BasisX")
        and hasattr(xform, "BasisY")
        and hasattr(xform, "BasisZ")
        and hasattr(xform, "Origin")
    ):
        bx = _to_vector3(xform.BasisX)
        by = _to_vector3(xform.BasisY)
        bz = _to_vector3(xform.BasisZ)
        orig = _to_vector3(xform.Origin)
        scale = float(getattr(xform, "Scale", 1.0) or 1.0)

        out = []
        for p in points:
            rx = scale * (p.x * bx.x + p.y * by.x + p.z * bz.x) + orig.x
            ry = scale * (p.x * bx.y + p.y * by.y + p.z * bz.y) + orig.y
            rz = scale * (p.x * bx.z + p.y * by.z + p.z * bz.z) + orig.z
            out.append(Vector3(rx, ry, rz))
        return out

    raise TypeError(
        "Unsupported transform object of type {0}: must provide transform_point, OfPoint, or BasisX/Y/Z+Origin.".format(
            type(xform)
        )
    )


def extract_element_host_bbox(revit_element, link_instance=None):
    """Extracts an element's BoundingBoxXYZ and transforms all 8 corners into host coordinates.

    Sequentially applies:
      1. bbox.Transform (if present on the element's BoundingBoxXYZ and non-identity)
      2. link_instance.GetTotalTransform() (if the element belongs to a RevitLinkInstance)

    Returns:
      (BoundingBox3D_or_None, diagnostics_list)
    """
    diagnostics = []

    if revit_element is None:
        return (None, ["Element is None; cannot extract BoundingBoxXYZ."])

    raw_bbox = None
    if hasattr(revit_element, "get_BoundingBox") and callable(revit_element.get_BoundingBox):
        try:
            raw_bbox = revit_element.get_BoundingBox(None)
        except Exception as ex:
            return (
                None,
                ["Failed calling get_BoundingBox(None) on element: {0}".format(ex)],
            )
    elif hasattr(revit_element, "BoundingBox"):
        raw_bbox = getattr(revit_element, "BoundingBox", None)

    if raw_bbox is None:
        return (None, ["Element has no BoundingBoxXYZ."])

    raw_min = getattr(raw_bbox, "Min", None)
    raw_max = getattr(raw_bbox, "Max", None)
    if raw_min is None or raw_max is None:
        return (None, ["Element BoundingBoxXYZ is missing Min or Max bounds."])

    try:
        local_box = BoundingBox3D(
            raw_min, raw_max, coord_space=COORD_SPACE_REVIT_INTERNAL
        )
    except Exception as ex:
        return (
            None,
            ["Element local BoundingBoxXYZ is invalid or inverted: {0}".format(ex)],
        )

    corners = local_box.corners()

    # Step 1: Apply bbox.Transform if present on the BoundingBoxXYZ itself
    bbox_xform = getattr(raw_bbox, "Transform", None)
    if bbox_xform is not None:
        try:
            corners = _apply_transform_to_points(corners, bbox_xform)
        except Exception as ex:
            return (
                None,
                ["Failed applying BoundingBoxXYZ.Transform to 8 corners: {0}".format(ex)],
            )

    # Step 2: Apply link_instance.GetTotalTransform() if element is in a linked model
    if link_instance is not None:
        link_xform = None
        if hasattr(link_instance, "GetTotalTransform") and callable(link_instance.GetTotalTransform):
            try:
                link_xform = link_instance.GetTotalTransform()
            except Exception as ex:
                return (
                    None,
                    ["Failed calling link_instance.GetTotalTransform(): {0}".format(ex)],
                )
        elif hasattr(link_instance, "GetTransform") and callable(link_instance.GetTransform):
            try:
                link_xform = link_instance.GetTransform()
            except Exception as ex:
                return (
                    None,
                    ["Failed calling link_instance.GetTransform(): {0}".format(ex)],
                )

        if link_xform is not None:
            try:
                corners = _apply_transform_to_points(corners, link_xform)
            except Exception as ex:
                return (
                    None,
                    ["Failed applying link instance transform to 8 corners: {0}".format(ex)],
                )

    min_x = min(p.x for p in corners)
    min_y = min(p.y for p in corners)
    min_z = min(p.z for p in corners)
    max_x = max(p.x for p in corners)
    max_y = max(p.y for p in corners)
    max_z = max(p.z for p in corners)

    host_bbox = BoundingBox3D(
        Vector3(min_x, min_y, min_z),
        Vector3(max_x, max_y, max_z),
        coord_space=COORD_SPACE_REVIT_INTERNAL,
    )
    return (host_bbox, diagnostics)


def evaluate_clash_geometry_corroboration(
    bbox_a, bbox_b, clash_point_ft=None, proximity_tol_ft=1.0
):
    """Evaluates spatial proximity of resolved participant bounding boxes and clash point.

    CRITICAL RULE:
      Returns supporting diagnostic data ONLY. Never use bounding-box intersection or
      clash-point containment to automatically upgrade an AMBIGUOUS link-instance
      resolution to RESOLVED, nor to declare VERIFIED_COORDINATE_FRAME without explicit
      transform verification.
    """
    tol = float(proximity_tol_ft)
    diagnostics = []
    has_both = isinstance(bbox_a, BoundingBox3D) and isinstance(bbox_b, BoundingBox3D)

    bboxes_intersect = None
    bboxes_within_tolerance = None
    min_distance_ft = None
    clash_point_in_union = None

    if has_both:
        bboxes_intersect = bbox_a.intersects(bbox_b, tolerance_ft=0.0)
        min_distance_ft = bbox_a.min_distance_to(bbox_b)
        bboxes_within_tolerance = min_distance_ft <= tol

        if bboxes_intersect:
            diagnostics.append(
                "Corroboration: Participant host bounding boxes directly intersect (distance = 0.000 ft)."
            )
        elif bboxes_within_tolerance:
            diagnostics.append(
                "Corroboration: Participant host bounding boxes are within tolerance ({0:.3f} ft <= {1:.3f} ft).".format(
                    min_distance_ft, tol
                )
            )
        else:
            diagnostics.append(
                "Geometry warning: Participant host bounding boxes are separated by {0:.3f} ft (> {1:.3f} ft tolerance).".format(
                    min_distance_ft, tol
                )
            )

    target_envelope = None
    if has_both:
        target_envelope = bbox_a.union(bbox_b)
    elif isinstance(bbox_a, BoundingBox3D):
        target_envelope = bbox_a
    elif isinstance(bbox_b, BoundingBox3D):
        target_envelope = bbox_b

    if clash_point_ft is not None and target_envelope is not None:
        try:
            cp_vec = _to_vector3(clash_point_ft)
            clash_point_in_union = target_envelope.contains_point(
                cp_vec, tolerance_ft=tol
            )
            if clash_point_in_union:
                diagnostics.append(
                    "Corroboration: Transformed clash point lies within participant bounding envelope (tol={0:.2f} ft).".format(
                        tol
                    )
                )
            else:
                diagnostics.append(
                    "Coordinate notice: Transformed clash point lies outside participant bounding envelope (tol={0:.2f} ft).".format(
                        tol
                    )
                )
        except Exception as ex:
            diagnostics.append("Could not evaluate clash point containment: {0}".format(ex))

    return {
        "has_both_bboxes": has_both,
        "bboxes_intersect": bboxes_intersect,
        "bboxes_within_tolerance": bboxes_within_tolerance,
        "min_distance_ft": min_distance_ft,
        "clash_point_in_union": clash_point_in_union,
        "is_corroborating_only": True,
        "diagnostics": diagnostics,
    }


class ClashFramingPlan(object):
    """Structured result of Mode A (Pair) or Mode B (Context) clash view framing calculation."""

    def __init__(
        self,
        mode,
        is_valid,
        framing_method,
        isolation_method,
        coord_frame_status=CoordinateFrameStatus.UNVERIFIED_COORDINATE_FRAME,
        section_box=None,
        bbox_a=None,
        bbox_b=None,
        eye_position=None,
        forward_vector=None,
        up_vector=None,
        right_vector=None,
        corroboration=None,
        diagnostics=None,
    ):
        self.mode = mode
        self.is_valid = bool(is_valid)
        self.framing_method = framing_method
        self.isolation_method = isolation_method
        self.coord_frame_status = coord_frame_status
        self.section_box = section_box
        self.bbox_a = bbox_a
        self.bbox_b = bbox_b
        self.eye_position = eye_position
        self.forward_vector = forward_vector
        self.up_vector = up_vector
        self.right_vector = right_vector
        self.corroboration = dict(corroboration) if corroboration else {}
        self.diagnostics = list(diagnostics) if diagnostics else []

    @property
    def box_min(self):
        return self.section_box.min_pt if self.section_box else None

    @property
    def box_max(self):
        return self.section_box.max_pt if self.section_box else None

    def to_dict(self):
        return {
            "mode": self.mode,
            "is_valid": self.is_valid,
            "framing_method": self.framing_method,
            "isolation_method": self.isolation_method,
            "coord_frame_status": self.coord_frame_status,
            "section_box": self.section_box.to_dict() if self.section_box else None,
            "box_min": self.box_min.to_dict() if self.box_min else None,
            "box_max": self.box_max.to_dict() if self.box_max else None,
            "bbox_a": self.bbox_a.to_dict() if self.bbox_a else None,
            "bbox_b": self.bbox_b.to_dict() if self.bbox_b else None,
            "eye_position": self.eye_position.to_dict() if self.eye_position else None,
            "forward_vector": self.forward_vector.to_dict() if self.forward_vector else None,
            "up_vector": self.up_vector.to_dict() if self.up_vector else None,
            "right_vector": self.right_vector.to_dict() if self.right_vector else None,
            "corroboration": self.corroboration,
            "diagnostics": self.diagnostics,
        }

    def __getitem__(self, key):
        if hasattr(self, key):
            return getattr(self, key)
        d = self.to_dict()
        if key in d:
            return d[key]
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __repr__(self):
        return (
            "ClashFramingPlan(mode='{0}', valid={1}, framing='{2}', "
            "isolation='{3}', coord='{4}')".format(
                self.mode,
                self.is_valid,
                self.framing_method,
                self.isolation_method,
                self.coord_frame_status,
            )
        )


def _resolve_participant_bbox(res_or_bbox, label):
    """Extracts (BoundingBox3D_or_None, target_type_str, is_resolved_bool, diagnostics) from input."""
    if res_or_bbox is None:
        return (None, "UNKNOWN", False, ["{0} is None.".format(label)])

    if isinstance(res_or_bbox, BoundingBox3D):
        return (res_or_bbox, "HOST", True, [])

    # Duck-typed ParticipantResolution
    is_res = bool(getattr(res_or_bbox, "is_resolved", False))
    t_type = getattr(res_or_bbox, "target_type", "UNKNOWN") or "UNKNOWN"

    if not is_res:
        status = getattr(res_or_bbox, "status", "UNRESOLVED")
        return (
            None,
            t_type,
            False,
            [
                "{0} is not resolved (status={1}, target_type={2}); cannot extract participant geometry.".format(
                    label, status, t_type
                )
            ],
        )

    elem = getattr(res_or_bbox, "resolved_element", None)
    link_inst = getattr(res_or_bbox, "link_instance", None)
    bbox, diags = extract_element_host_bbox(elem, link_instance=link_inst)
    return (bbox, t_type, True, diags)


def calculate_clash_framing(
    res_a=None,
    res_b=None,
    mode="pair",
    pair_margin_ft=0.5,
    context_margin_ft=3.0,
    min_box_span_ft=4.0,
    max_box_span_ft=None,
    viewpoint=None,
    coord_frame_status=CoordinateFrameStatus.UNVERIFIED_COORDINATE_FRAME,
    bbox_a=None,
    bbox_b=None,
    clash_point_ft=None,
):
    """Calculates Mode A ('pair') or Mode B ('context') 3D section box and camera framing.

    Strictly bases section-box framing on resolved Revit element bounding boxes in host
    coordinates (never on unverified Navisworks clash point coordinates when element
    geometry is unavailable), and only reports EXACT_CAMERA_SYNC when both the viewpoint
    and coordinate frame status are explicitly verified.
    """
    clean_mode = str(mode).strip().lower() if mode else "pair"
    if clean_mode not in ("pair", "context"):
        raise ValueError(
            "Unsupported framing mode '{0}': expected 'pair' or 'context'.".format(mode)
        )

    diagnostics = []

    # Resolve bounding boxes and target types for Participant A and B
    if bbox_a is not None:
        box_a = bbox_a
        type_a = getattr(res_a, "target_type", "HOST") if res_a is not None else "HOST"
        resolved_a = True
    else:
        box_a, type_a, resolved_a, diags_a = _resolve_participant_bbox(
            res_a, "Participant A"
        )
        diagnostics.extend(diags_a)

    if bbox_b is not None:
        box_b = bbox_b
        type_b = getattr(res_b, "target_type", "HOST") if res_b is not None else "HOST"
        resolved_b = True
    else:
        box_b, type_b, resolved_b, diags_b = _resolve_participant_bbox(
            res_b, "Participant B"
        )
        diagnostics.extend(diags_b)

    margin = float(pair_margin_ft) if clean_mode == "pair" else float(context_margin_ft)
    min_span = float(min_box_span_ft)

    # Evaluate geometry corroboration (supporting diagnostics ONLY)
    corrob = evaluate_clash_geometry_corroboration(
        box_a, box_b, clash_point_ft=clash_point_ft
    )
    diagnostics.extend(corrob["diagnostics"])

    # Determine base bounding box and framing_method
    if box_a is not None and box_b is not None:
        span_a = max(box_a.size.x, box_a.size.y, box_a.size.z)
        span_b = max(box_b.size.x, box_b.size.y, box_b.size.z)
        continuous_thresh = 60.0

        if span_a <= continuous_thresh and span_b <= continuous_thresh:
            # Both elements are standard-sized members (e.g. beam, ceiling, duct, pipe, column).
            # Frame the FULL union of both elements so neither element is cropped into!
            base_box = box_a.union(box_b)
            framing_method = FramingMethod.AUTO_FRAMED_PARTICIPANTS
        elif span_a <= continuous_thresh and span_b > continuous_thresh:
            # Participant B is a massive continuous structure (e.g. 250 ft floor slab, ground terrain).
            # Frame tightly on Participant A so the section box does not encompass the entire floor.
            base_box = box_a
            framing_method = FramingMethod.AUTO_FRAMED_PARTICIPANTS
            diagnostics.append(
                "Focused framing: Participant B is a continuous element (span={0:.1f} ft); section box framed tightly on Participant A (span={1:.1f} ft).".format(
                    span_b, span_a
                )
            )
        elif span_b <= continuous_thresh and span_a > continuous_thresh:
            # Participant A is a massive continuous structure. Frame on Participant B.
            base_box = box_b
            framing_method = FramingMethod.AUTO_FRAMED_PARTICIPANTS
            diagnostics.append(
                "Focused framing: Participant A is a continuous element (span={0:.1f} ft); section box framed tightly on Participant B (span={1:.1f} ft).".format(
                    span_a, span_b
                )
            )
        else:
            # Both elements are massive continuous structures (e.g. Slab vs Wall)
            inter = box_a.intersection(box_b) if hasattr(box_a, "intersection") else None
            has_valid_inter = inter is not None and not inter.is_degenerate(tol=1e-4)
            if has_valid_inter:
                base_box = inter
                framing_method = FramingMethod.AUTO_FRAMED_PARTICIPANTS
                diagnostics.append(
                    "Focused framing: Both participants are continuous elements; section box framed on 3D collision intersection."
                )
            elif clash_point_ft is not None:
                try:
                    cp_vec = Vector3(float(clash_point_ft.x), float(clash_point_ft.y), float(clash_point_ft.z))
                    half = min_span * 0.5
                    base_box = BoundingBox3D(
                        cp_vec - Vector3(half, half, half),
                        cp_vec + Vector3(half, half, half),
                    )
                    framing_method = FramingMethod.AUTO_FRAMED_PARTICIPANTS
                    diagnostics.append(
                        "Focused framing: Centered section box on clash point."
                    )
                except Exception:
                    base_box = box_a if span_a <= span_b else box_b
                    framing_method = FramingMethod.AUTO_FRAMED_PARTICIPANTS
            else:
                base_box = box_a if span_a <= span_b else box_b
                framing_method = FramingMethod.AUTO_FRAMED_PARTICIPANTS
    elif box_a is not None:
        base_box = box_a
        framing_method = FramingMethod.SINGLE_PARTICIPANT_FALLBACK
        diagnostics.append(
            "Single-participant fallback: Framed around Participant A only because Participant B has no valid resolved bounding box."
        )
    elif box_b is not None:
        base_box = box_b
        framing_method = FramingMethod.SINGLE_PARTICIPANT_FALLBACK
        diagnostics.append(
            "Single-participant fallback: Framed around Participant B only because Participant A has no valid resolved bounding box."
        )
    else:
        diagnostics.append(
            "Unavailable geometry fallback: Neither Participant A nor Participant B has a valid resolved host BoundingBox3D. Refusing to fabricate participant framing."
        )
        return ClashFramingPlan(
            mode=clean_mode,
            is_valid=False,
            framing_method=FramingMethod.UNAVAILABLE_GEOMETRY_FALLBACK,
            isolation_method=IsolationMethod.NONE,
            coord_frame_status=coord_frame_status,
            section_box=None,
            bbox_a=None,
            bbox_b=None,
            eye_position=None,
            forward_vector=None,
            up_vector=None,
            right_vector=None,
            corroboration=corrob,
            diagnostics=diagnostics,
        )

    # Expand by mode margin and enforce minimum box dimensions centered on box
    section_box = base_box.expand(margin).enforce_min_dimensions(min_span)

    # Enforce maximum section box span cap so views never frame an entire floor/building
    max_cap = float(max_box_span_ft) if max_box_span_ft is not None else (18.0 if clean_mode == "pair" else 35.0)
    if max_cap > 0.0:
        s = section_box.size
        if s.x > max_cap or s.y > max_cap or s.z > max_cap:
            c = section_box.center
            hx = 0.5 * min(s.x, max_cap)
            hy = 0.5 * min(s.y, max_cap)
            hz = 0.5 * min(s.z, max_cap)
            section_box = BoundingBox3D(
                c - Vector3(hx, hy, hz),
                c + Vector3(hx, hy, hz),
                coord_space=section_box.coord_space,
            )

    # Determine honest isolation_method
    if clean_mode == "context":
        isolation_method = IsolationMethod.CONTEXT_SECTION_BOX_ONLY
    else:
        # Mode A ('pair')
        active_types = []
        if box_a is not None:
            active_types.append(type_a)
        if box_b is not None:
            active_types.append(type_b)

        involves_link = any(t in ("LINK", "NESTED_LINK") for t in active_types)
        if involves_link:
            isolation_method = IsolationMethod.LINK_CATEGORY_AND_SECTION_BOX
            diagnostics.append(
                "Isolation notice: Clash involves linked element(s). Revit 2024's native view API cannot isolate individual elements inside a RevitLinkInstance; using LINK_CATEGORY_AND_SECTION_BOX."
            )
        else:
            isolation_method = IsolationMethod.NATIVE_ELEMENT_ISOLATION

    # Determine camera orientation and whether viewpoint qualifies for EXACT_CAMERA_SYNC
    used_exact_camera = False
    eye_pos = None
    forward_vec = None
    up_vec = None
    right_vec = None

    if viewpoint is not None:
        vp_units_ok = bool(getattr(viewpoint, "units_verified", True))
        coord_verified = coord_frame_status in (
            CoordinateFrameStatus.VERIFIED_INTERNAL,
            CoordinateFrameStatus.VERIFIED_SHARED,
        )
        vp_pos = getattr(viewpoint, "position", None)

        f_raw = getattr(viewpoint, "forward_vector", None)
        u_raw = getattr(viewpoint, "up_vector", None)
        quat = getattr(viewpoint, "quaternion", None)

        if (not f_raw or not u_raw) and quat and len(quat) >= 4:
            try:
                q_obj = Quaternion(quat[0], quat[1], quat[2], quat[3])
                qf, qu, _ = q_obj.to_camera_vectors()
                f_raw = f_raw or qf.to_tuple()
                u_raw = u_raw or qu.to_tuple()
            except Exception:
                pass

        if not coord_verified:
            diagnostics.append(
                "Saved viewpoint was not used for EXACT_CAMERA_SYNC because coordinate frame status is '{0}' (unverified). Using deterministic isometric framing around resolved Revit geometry.".format(
                    coord_frame_status
                )
            )
        elif not vp_units_ok:
            diagnostics.append(
                "Saved viewpoint was not used for EXACT_CAMERA_SYNC because viewpoint units were defaulted/unverified."
            )
        elif vp_pos is None or not f_raw or not u_raw:
            diagnostics.append(
                "Saved viewpoint is missing camera position or orientation vectors; falling back to isometric framing."
            )
        else:
            try:
                vp_unit = getattr(viewpoint, "units", None) or getattr(vp_pos, "units", "m")
                scale_ft = get_feet_scale_factor(vp_unit)
                px, py, pz = _extract_xyz(vp_pos)
                eye_candidate = Vector3(px * scale_ft, py * scale_ft, pz * scale_ft)

                f_vec = _to_vector3(f_raw)
                u_vec = _to_vector3(u_raw)
                f_norm, u_ortho, r_norm, was_adj = validate_and_orthogonalize_camera(
                    f_vec, u_vec
                )
                if was_adj:
                    diagnostics.append(
                        "Camera up vector was orthogonalized via Gram-Schmidt to satisfy Revit ViewOrientation3D requirements."
                    )

                eye_pos = eye_candidate
                forward_vec = f_norm
                up_vec = u_ortho
                right_vec = r_norm
                framing_method = FramingMethod.EXACT_CAMERA_SYNC
                used_exact_camera = True
            except Exception as ex:
                diagnostics.append(
                    "Saved viewpoint camera validation failed ({0}); falling back to isometric framing.".format(
                        ex
                    )
                )

    if not used_exact_camera:
        center = section_box.center
        forward_vec = Vector3(-1.0, 1.0, -0.8).normalize()
        world_z = Vector3(0.0, 0.0, 1.0)
        up_raw = world_z - forward_vec * forward_vec.dot(world_z)
        if up_raw.length_squared() < 1e-4:
            up_raw = Vector3(0.0, 1.0, 0.0)
        up_vec = up_raw.normalize()
        right_vec = forward_vec.cross(up_vec).normalize()

        diag_span = max(section_box.size.length(), min_span)
        eye_pos = center - forward_vec * (diag_span * 1.75)

    return ClashFramingPlan(
        mode=clean_mode,
        is_valid=True,
        framing_method=framing_method,
        isolation_method=isolation_method,
        coord_frame_status=coord_frame_status,
        section_box=section_box,
        bbox_a=box_a,
        bbox_b=box_b,
        eye_position=eye_pos,
        forward_vector=forward_vec,
        up_vector=up_vec,
        right_vector=right_vec,
        corroboration=corrob,
        diagnostics=diagnostics,
    )
