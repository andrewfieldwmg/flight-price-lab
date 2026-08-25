export function canPrepareBooking({
  exactFlightVerified,
  passengerCompositionVerified,
}) {
  return exactFlightVerified === true && passengerCompositionVerified === true;
}
