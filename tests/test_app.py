import pytest
from unittest.mock import patch, MagicMock
from app import fetch_espn_league_data, fetch_fantasypros_data


@patch('app.requests.get')
def test_fetch_espn_league_data(mock_get):
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"teams": [], "schedule": []}
    mock_get.return_value = mock_response

    data = fetch_espn_league_data("123", "2024", swid="test_swid", espn_s2="test_s2")
    assert data == {"teams": [], "schedule": []}
    mock_get.assert_called_once()


@patch('app.requests.get')
def test_fetch_fantasypros_data(mock_get):
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"standings": []}
    mock_get.return_value = mock_response

    data = fetch_fantasypros_data("test_key")
    assert data == {"standings": []}
    mock_get.assert_called_once()


@patch('app.requests.get')
def test_fetch_fantasypros_data_no_key(mock_get):
    data = fetch_fantasypros_data()
    assert data is None
    mock_get.assert_not_called()