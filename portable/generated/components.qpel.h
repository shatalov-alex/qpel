// Generated from qpel IR. Do not edit.
// qpel numeric contract: u32-modulo-v1
QPEL_KERNEL void initialize(QPEL_PARAM(QPEL_READ_U8, mask, 0), QPEL_PARAM(QPEL_ATOMIC_U32, parent, 1), QPEL_PARAM(QPEL_ATOMIC_U32, counts, 2), QPEL_PARAM(QPEL_SHAPE, shape, 3) QPEL_GRID_PARAM(qpel_index)) {
QPEL_GRID_LOCAL(qpel_index)
{
if (((QPEL_U32)(qpel_index) >= (QPEL_U32)(((QPEL_U32)(((QPEL_U32)(shape.x) * (QPEL_U32)(shape.y))) * (QPEL_U32)(shape.z))))) {
return ;
 }
QPEL_STORE((parent + (QPEL_U32)(qpel_index)), qpel_index);
QPEL_STORE((counts + (QPEL_U32)(qpel_index)), 0u);
 }
}

QPEL_DEVICE QPEL_U32 root(QPEL_ATOMIC_U32 p, QPEL_U32 i) {
{
QPEL_U32 next = QPEL_LOAD((p + (QPEL_U32)(i)));
while (((QPEL_U32)(next) != (QPEL_U32)(i))) {
i = next;
next = QPEL_LOAD((p + (QPEL_U32)(i)));
 }
return i;
 }
}

QPEL_DEVICE void unite(QPEL_ATOMIC_U32 p, QPEL_U32 a, QPEL_U32 b) {
{
while (true) {
a = root(p, a);
b = root(p, b);
if (((QPEL_U32)(a) == (QPEL_U32)(b))) {
return ;
 }
QPEL_U32 lo = min((QPEL_U32)(a), (QPEL_U32)(b));
QPEL_U32 hi = max((QPEL_U32)(a), (QPEL_U32)(b));
QPEL_U32 old = QPEL_MIN((p + (QPEL_U32)(hi)), lo);
if (((QPEL_U32)(old) == (QPEL_U32)(hi))) {
return ;
 }
a = old;
b = lo;
 }
 }
}

QPEL_KERNEL void merge(QPEL_PARAM(QPEL_READ_U8, mask, 0), QPEL_PARAM(QPEL_ATOMIC_U32, parent, 1), QPEL_PARAM(QPEL_ATOMIC_U32, counts, 2), QPEL_PARAM(QPEL_SHAPE, shape, 3) QPEL_GRID_PARAM(qpel_index)) {
QPEL_GRID_LOCAL(qpel_index)
{
QPEL_U32 w = shape.x;
QPEL_U32 h = shape.y;
if ((((QPEL_U32)(qpel_index) >= (QPEL_U32)(((QPEL_U32)(((QPEL_U32)(w) * (QPEL_U32)(h))) * (QPEL_U32)(shape.z)))) || (!mask[qpel_index]))) {
return ;
 }
QPEL_U32 x = ((QPEL_U32)(qpel_index) % (QPEL_U32)(w));
QPEL_U32 y = ((QPEL_U32)(((QPEL_U32)(qpel_index) / (QPEL_U32)(w))) % (QPEL_U32)(h));
if ((((QPEL_U32)(x) > (QPEL_U32)(0u)) && mask[((QPEL_U32)(qpel_index) - (QPEL_U32)(1u))])) {
unite(parent, qpel_index, ((QPEL_U32)(qpel_index) - (QPEL_U32)(1u)));
 }
if (((QPEL_U32)(y) > (QPEL_U32)(0u))) {
if (mask[((QPEL_U32)(qpel_index) - (QPEL_U32)(w))]) {
unite(parent, qpel_index, ((QPEL_U32)(qpel_index) - (QPEL_U32)(w)));
 }
if ((((QPEL_U32)(x) > (QPEL_U32)(0u)) && mask[((QPEL_U32)(((QPEL_U32)(qpel_index) - (QPEL_U32)(w))) - (QPEL_U32)(1u))])) {
unite(parent, qpel_index, ((QPEL_U32)(((QPEL_U32)(qpel_index) - (QPEL_U32)(w))) - (QPEL_U32)(1u)));
 }
if ((((QPEL_U32)(((QPEL_U32)(x) + (QPEL_U32)(1u))) < (QPEL_U32)(w)) && mask[((QPEL_U32)(((QPEL_U32)(qpel_index) - (QPEL_U32)(w))) + (QPEL_U32)(1u))])) {
unite(parent, qpel_index, ((QPEL_U32)(((QPEL_U32)(qpel_index) - (QPEL_U32)(w))) + (QPEL_U32)(1u)));
 }
 }
 }
}

QPEL_KERNEL void label_count(QPEL_PARAM(QPEL_READ_U8, mask, 0), QPEL_PARAM(QPEL_ATOMIC_U32, parent, 1), QPEL_PARAM(QPEL_ATOMIC_U32, counts, 2), QPEL_PARAM(QPEL_SHAPE, shape, 3), QPEL_PARAM(QPEL_WRITE_U32, labels, 4) QPEL_GRID_PARAM(qpel_index)) {
QPEL_GRID_LOCAL(qpel_index)
{
if (((QPEL_U32)(qpel_index) >= (QPEL_U32)(((QPEL_U32)(((QPEL_U32)(shape.x) * (QPEL_U32)(shape.y))) * (QPEL_U32)(shape.z))))) {
return ;
 }
if ((!mask[qpel_index])) {
labels[qpel_index] = 0u;
return ;
 }
QPEL_U32 r = root(parent, qpel_index);
labels[qpel_index] = ((QPEL_U32)(((QPEL_U32)(r) % (QPEL_U32)(((QPEL_U32)(shape.x) * (QPEL_U32)(shape.y))))) + (QPEL_U32)(1u));
QPEL_ADD((counts + (QPEL_U32)(r)), 1u);
 }
}

QPEL_KERNEL void areas(QPEL_PARAM(QPEL_READ_U8, mask, 0), QPEL_PARAM(QPEL_ATOMIC_U32, parent, 1), QPEL_PARAM(QPEL_ATOMIC_U32, counts, 2), QPEL_PARAM(QPEL_SHAPE, shape, 3), QPEL_PARAM(QPEL_WRITE_U32, labels, 4), QPEL_PARAM(QPEL_WRITE_U32, output, 5) QPEL_GRID_PARAM(qpel_index)) {
QPEL_GRID_LOCAL(qpel_index)
{
QPEL_U32 plane = ((QPEL_U32)(shape.x) * (QPEL_U32)(shape.y));
if (((QPEL_U32)(qpel_index) >= (QPEL_U32)(((QPEL_U32)(plane) * (QPEL_U32)(shape.z))))) {
return ;
 }
QPEL_U32 label = labels[qpel_index];
output[qpel_index] = (label ? QPEL_LOAD((((counts + (QPEL_U32)(((QPEL_U32)(((QPEL_U32)(qpel_index) / (QPEL_U32)(plane))) * (QPEL_U32)(plane)))) + (QPEL_U32)(label)) - (QPEL_U32)(1u))) : 0u);
 }
}
