interface DateFieldsProps {
  outbound: string;
  inbound: string;
  roundTrip: boolean;
  onOutboundChange: (value: string) => void;
  onInboundChange: (value: string) => void;
}

export function DateFields({
  outbound,
  inbound,
  roundTrip,
  onOutboundChange,
  onInboundChange,
}: DateFieldsProps) {
  return (
    <>
      <label className="compact-field">
        <span>Out</span>
        <input type="date" value={outbound} onChange={(e) => onOutboundChange(e.target.value)} required />
      </label>
      <label className="compact-field">
        <span>Return</span>
        <input
          type="date"
          value={inbound}
          min={outbound}
          onChange={(e) => onInboundChange(e.target.value)}
          disabled={!roundTrip}
          required={roundTrip}
        />
      </label>
    </>
  );
}
