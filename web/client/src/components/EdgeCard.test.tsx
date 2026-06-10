import { render, screen } from '@testing-library/react';
import { EdgeCard } from './EdgeCard';

describe('EdgeCard Component', () => {
  const defaultProps = {
    player: 'A\'ja Wilson',
    team: 'LVA',
    stat: 'Points',
    line: 22.5,
    projection: 25.1,
    bookOdds: -110,
    trueOdds: 58.5,
    edge: 3.2,
    isOver: true,
  };

  it('renders standard props correctly', () => {
    render(<EdgeCard {...defaultProps} />);

    expect(screen.getByText('A\'ja Wilson')).toBeInTheDocument();
    expect(screen.getByText('LVA')).toBeInTheDocument();
    expect(screen.getByText('Points')).toBeInTheDocument();
    expect(screen.getByText('O 22.5')).toBeInTheDocument();
    expect(screen.getByText('25.1')).toBeInTheDocument();
    expect(screen.getByText('58.5%')).toBeInTheDocument();
    expect(screen.getByText('3.2 Edge')).toBeInTheDocument();
  });

  it('applies glowing success class when edge > 5', () => {
    const { container } = render(<EdgeCard {...defaultProps} edge={6.5} />);

    // Check if the glow class is applied to the root card
    const card = container.firstChild as HTMLElement;
    expect(card).toHaveClass('shadow-[0_0_15px_rgba(34,197,94,0.15)]');

    // Check if edge text is success color
    const edgeText = screen.getByText('6.5 Edge');
    expect(edgeText.parentElement).toHaveClass('text-success');
    expect(edgeText.parentElement).not.toHaveClass('text-primary');
  });

  it('applies primary text and no glow when edge <= 5', () => {
    const { container } = render(<EdgeCard {...defaultProps} edge={3.2} />);

    const card = container.firstChild as HTMLElement;
    expect(card).not.toHaveClass('shadow-[0_0_15px_rgba(34,197,94,0.15)]');

    const edgeText = screen.getByText('3.2 Edge');
    expect(edgeText.parentElement).toHaveClass('text-primary');
    expect(edgeText.parentElement).not.toHaveClass('text-success');
  });

  it('renders "Over" UI elements correctly when isOver is true', () => {
    render(<EdgeCard {...defaultProps} isOver={true} line={22.5} bookOdds={-110} />);

    expect(screen.getByText('O 22.5')).toBeInTheDocument();
    expect(screen.getByText('Bet Over @ -110')).toBeInTheDocument();

    // Verify TrendingUp icon is present. We can query for its class or container.
    // The TrendingUp icon has text-success class.
    const iconContainer = screen.getByText('Bet Over @ -110').parentElement;
    expect(iconContainer?.querySelector('.lucide-trending-up')).toBeInTheDocument();
    expect(iconContainer?.querySelector('.text-success')).toBeInTheDocument();
  });

  it('renders "Under" UI elements correctly when isOver is false', () => {
    render(<EdgeCard {...defaultProps} isOver={false} line={22.5} bookOdds={-110} />);

    expect(screen.getByText('U 22.5')).toBeInTheDocument();
    expect(screen.getByText('Bet Under @ -110')).toBeInTheDocument();

    // Verify TrendingDown icon is present.
    const iconContainer = screen.getByText('Bet Under @ -110').parentElement;
    expect(iconContainer?.querySelector('.lucide-trending-down')).toBeInTheDocument();
    expect(iconContainer?.querySelector('.text-danger')).toBeInTheDocument();
  });

  it('adds a plus sign for positive bookOdds', () => {
    render(<EdgeCard {...defaultProps} bookOdds={120} />);
    expect(screen.getByText('Bet Over @ +120')).toBeInTheDocument();
  });

  it('does not add a plus sign for negative bookOdds', () => {
    render(<EdgeCard {...defaultProps} bookOdds={-110} />);
    expect(screen.getByText('Bet Over @ -110')).toBeInTheDocument();
  });
});
