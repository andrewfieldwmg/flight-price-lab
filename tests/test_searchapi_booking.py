from decimal import Decimal

from flight_price_lab.providers.searchapi_booking import parse_booking_options


def test_parses_known_booking_option_fields() -> None:
    options = parse_booking_options(
        {
            "booking_options": [
                {
                    "fare_type": "Basic",
                    "price": 258,
                    "baggage_prices": {"carry_on": 40, "checked": 70},
                    "is_split_booking": False,
                    "booking_provider": "Example Travel",
                }
            ]
        }
    )

    assert len(options) == 1
    assert options[0].fare_type == "Basic"
    assert options[0].price == Decimal(258)
    assert options[0].baggage_prices == {"carry_on": 40, "checked": 70}
    assert options[0].is_split_booking is False
    assert options[0].booking_provider == "Example Travel"
